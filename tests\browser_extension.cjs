// Offline extension contract checks; no connected browser or account required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const path = require('node:path');
const assets = path.join(__dirname, '../assets/browser_extension');

async function run() {
  let allowed = false, searches = 0;
  const background = vm.createContext({URL, navigator: {userAgent: 'Chrome'}, chrome: {
    runtime: {onMessage: {addListener() {}}},
    permissions: {contains: async () => allowed},
    downloads: {search: async () => { searches++; return [
      {id: 1, filename: 'lesson.pdf', url: 'https://example.com/file?token=private-token', incognito: false},
      {id: 2, filename: 'private.pdf', url: 'https://example.com/private', incognito: true}]; }}
  }});
  vm.runInContext(fs.readFileSync(path.join(assets, 'background.js'), 'utf8'), background);
  await assert.rejects(background.dispatch('downloads', {limit: 10}), /Downloads access/);
  assert.equal(searches, 0);
  allowed = true;
  await assert.rejects(background.dispatch('downloads', {limit: 31}));
  const result = await background.dispatch('downloads', {limit: 10});
  assert.equal(result.items.length, 1);
  assert(!JSON.stringify(result).includes('private-token'));
  let closed = false;
  background.chrome.tabs = {
    get: async id => ({id, url: id === 1 ? 'https://example.com/original' : 'https://example.com/changed'}),
    remove: async () => { closed = true; }
  };
  await assert.rejects(background.dispatch('tab_action', {tab_id: 2, action: 'close', duplicate_of: 1}), /duplicates/);
  assert.equal(closed, false);

  const form = {innerText: 'PRIVATE FORM VALUE', getAttribute: () => null, querySelector: () => null};
  const field = {tagName: 'TEXTAREA', type: '', innerText: 'PRIVATE FIELD VALUE', labels: [],
    required: true, readOnly: false, isConnected: true,
    getBoundingClientRect: () => ({width: 30, height: 20}),
    getAttribute: name => name === 'placeholder' ? 'Comment' : null,
    closest: selector => selector === 'form' ? form : null,
    matches: () => true};
  const button = {...field, tagName: 'BUTTON', innerText: 'Save', required: false,
    getAttribute: () => null, matches: () => false};
  const location = {pathname: '/article', hostname: 'example.com', href: 'https://example.com/article'};
  const page = vm.createContext({crypto: webcrypto, performance, location, NodeFilter: {SHOW_TEXT: 4},
    getComputedStyle: () => ({visibility: 'visible', display: 'block'}),
    document: {title: 'Article', querySelectorAll: () => [field, button],
      createTreeWalker: () => ({nextNode: () => null})}});
  vm.runInContext(fs.readFileSync(path.join(assets, 'page.js'), 'utf8'), page);
  const inspected = page.__jarvixPage.dispatch('inspect', {});
  assert.equal(inspected.forms[0].name, 'Form');
  assert.equal(inspected.forms[0].fields[0].name, 'Comment');
  assert.equal(inspected.forms[0].fields[0].required, true);
  assert(!JSON.stringify(inspected).includes('PRIVATE'));
  const ref = inspected.elements[0].ref;
  field.type = 'password';
  assert.throws(() => page.__jarvixPage.dispatch('act', {
    ref, document: inspected.document, action: 'type', text: 'never'}), /Protected/);
  location.pathname = '/login';
  const blocked = page.__jarvixPage.dispatch('inspect', {});
  assert.equal(blocked.blocked, true);
  assert.equal(blocked.elements.length, 0);
  assert.equal(blocked.forms.length, 0);
}
run().catch(error => { console.error(error); process.exitCode = 1; });
