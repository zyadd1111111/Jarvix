/* All operations are fixed extension APIs. No caller-supplied scripts/selectors. */
let port = null, connected = false, lastError = '';
const browserName = navigator.userAgent.includes('Edg/') ? 'edge' : 'chrome';
const safeURL = value => {
  try {
    const u = new URL(value);
    if (u.username || u.password) return '[credential URL hidden]';
    if (!['http:', 'https:', 'about:'].includes(u.protocol)) return '[internal browser page]';
    for (const key of [...u.searchParams.keys()]) if (/password|secret|token|credential|code|state|key|auth|session|sid/i.test(key)) u.searchParams.set(key, '[redacted]');
    u.hash = ''; return u.href.slice(0, 4096);
  } catch { return '[invalid URL]'; }
};
const tabInfo = t => ({id: String(t.id), title: (t.title || '').slice(0, 300), url: safeURL(t.url || ''),
  active: !!t.active, window_id: t.windowId, group_id: t.groupId});
function publicURL(value) {
  const u = new URL(value);
  if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password || /[\x00-\x20]/.test(value)) throw Error('Use a public HTTP(S) URL.');
  return u.href;
}
async function knownTab(id, content = false) {
  if (!Number.isSafeInteger(id) || id < 1) throw Error('Choose a known tab.');
  const tab = await chrome.tabs.get(id);
  if (tab.incognito) throw Error('Private browsing tabs cannot be connected.');
  if (content && !/^https?:\/\//i.test(tab.url || '')) throw Error('Internal browser pages cannot be inspected or automated.');
  return tab;
}
async function page(id, operation, args) {
  await knownTab(id, true);
  await chrome.scripting.executeScript({target: {tabId: id, frameIds: [0]}, files: ['page.js'], world: 'ISOLATED'});
  const values = await chrome.scripting.executeScript({target: {tabId: id, frameIds: [0]}, world: 'ISOLATED',
    func: (op, data) => globalThis.__jarvixPage.dispatch(op, data), args: [operation, args]});
  if (!values.length || !values[0].result) throw Error('Page changed or is unavailable; inspect it again.');
  return values[0].result;
}
async function dispatch(operation, args) {
  if (operation === 'tabs') return {items: (await chrome.tabs.query({})).filter(t => !t.incognito).slice(0, 100).map(tabInfo)};
  if (operation === 'active_tab') {
    const window = await chrome.windows.getLastFocused();
    if (!window.focused) return {available: false, reason: 'The browser is not the foreground application.'};
    const tab = (await chrome.tabs.query({active: true, windowId: window.id})).find(t => !t.incognito);
    return tab ? {available: true, ...tabInfo(tab)} : {available: false};
  }
  if (operation === 'open_tab') {
    const tab = await chrome.tabs.create({url: publicURL(args.url)});
    return {id: String(tab.id), opened: true, verified: !!(await chrome.tabs.get(tab.id))};
  }
  if (operation === 'tab_action') {
    const tab = await knownTab(args.tab_id);
    if (args.action === 'switch') {
      await chrome.windows.update(tab.windowId, {focused: true}); await chrome.tabs.update(tab.id, {active: true});
      return {performed: true, verified: (await chrome.tabs.get(tab.id)).active};
    }
    if (args.action === 'duplicate') {
      publicURL(tab.url); const copy = await chrome.tabs.duplicate(tab.id);
      return {performed: true, id: String(copy.id), verified: true};
    }
    if (args.action === 'close') {
      await chrome.tabs.remove(tab.id);
      const exists = (await chrome.tabs.query({})).some(t => t.id === tab.id);
      return {performed: !exists, verified: !exists};
    }
    if (args.action === 'reload') await chrome.tabs.reload(tab.id);
    else if (args.action === 'back') await chrome.tabs.goBack(tab.id);
    else if (args.action === 'forward') await chrome.tabs.goForward(tab.id);
    else throw Error('Unsupported tab action.');
    return {performed: true, verified: false, note: 'Navigation requested; inspect the resulting page to verify.'};
  }
  if (operation === 'group') {
    if (!Array.isArray(args.tab_ids) || !args.tab_ids.length || args.tab_ids.length > 30) throw Error('Select 1–30 tabs.');
    const tabs = await Promise.all(args.tab_ids.map(id => knownTab(id)));
    if (new Set(tabs.map(t => t.windowId)).size !== 1) throw Error('Group tabs within one browser window.');
    const groupId = await chrome.tabs.group({tabIds: args.tab_ids});
    await chrome.tabGroups.update(groupId, {title: String(args.title).slice(0, 100), color: args.color});
    const actual = await chrome.tabs.query({groupId});
    return {group_id: groupId, tab_count: actual.length, verified: actual.length === tabs.length};
  }
  if (['inspect', 'act', 'selection', 'scroll'].includes(operation)) return page(args.tab_id, operation, args);
  throw Error('Unsupported browser operation.');
}
function disconnect() {
  if (port) port.disconnect(); port = null; connected = false;
  chrome.action.setBadgeText({text: ''});
}
function connect() {
  disconnect(); lastError = '';
  const activePort = chrome.runtime.connectNative('com.jarvix.browser'); port = activePort;
  activePort.onDisconnect.addListener(() => {
    const error = chrome.runtime.lastError;
    if (port === activePort) { connected = false; port = null; lastError = error ? 'Open Jarvix and check native bridge registration.' : 'Disconnected'; chrome.action.setBadgeText({text: ''}); }
  });
  activePort.onMessage.addListener(async message => {
    if (message.connected) { connected = true; chrome.action.setBadgeText({text: 'ON'}); chrome.action.setBadgeBackgroundColor({color: '#51458a'}); return; }
    if (!connected || typeof message.id !== 'string' || !message.arguments || typeof message.arguments !== 'object') return;
    let result;
    try { result = await dispatch(message.operation, message.arguments); }
    catch { result = {ok: false, error: 'Browser action unavailable, protected or stale. Inspect the current tab and retry.'}; }
    if (port === activePort) activePort.postMessage({id: message.id, result});
  });
  activePort.postMessage({browser: browserName});
}
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id || !sender.url?.startsWith(chrome.runtime.getURL(''))) return false;
  if (message.action === 'connect') connect();
  if (message.action === 'disconnect') disconnect();
  reply({connected, error: lastError}); return false;
});
// No startup reconnect or tab/activity listeners: connection is a visible opt-in.

