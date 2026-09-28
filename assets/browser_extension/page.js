/* Runs only on explicit requests, in the extension's isolated world. */
(() => {
  if (globalThis.__jarvixPage) return;
  const documentId = crypto.randomUUID(), refs = new Map();
  const sensitive = /password|passwd|secret|token|credential|captcha|verification|one.?time|security.?code|credit.?card|card.?number|cvc|cvv|two.?factor|authenticator/i;
  const pageProtected = () => /\/(?:login|signin|sign-in|oauth|authorize|captcha|challenge|password|checkout|payment)(?:[/?#]|$)/i.test(location.pathname) || /^(accounts\.google\.com|login\.microsoftonline\.com)$/i.test(location.hostname);
  const signature = e => [e.tagName, e.getAttribute('role') || '', e.getAttribute('aria-label') || '',
    e.getAttribute('name') || '', e.type || '', e.getAttribute('href') || '', e.innerText || e.textContent || ''].join('|').slice(0, 2000);
  const protectedElement = e => {
    const identifiers = [e.type, e.name, e.id, e.getAttribute('autocomplete'), e.getAttribute('aria-label'),
      ...(e.labels ? [...e.labels].map(l => l.textContent) : [])].join(' ');
    const form = e.closest('form');
    return pageProtected() || e.type === 'password' || sensitive.test(identifiers) ||
      !!e.closest('[data-private],[data-sensitive],iframe') ||
      !!(form && (form.querySelector('input[type=password],input[autocomplete*=password],input[autocomplete=one-time-code],input[autocomplete^=cc-]') || sensitive.test(form.getAttribute('aria-label') || ''))) ||
      sensitive.test((e.innerText || '').slice(0, 300));
  };
  const visible = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e); return e.isConnected && r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const name = e => (e.getAttribute('aria-label') || (e.labels ? [...e.labels].map(l => l.textContent).join(' ') : '') || e.innerText || e.getAttribute('title') || e.getAttribute('placeholder') || '').trim().slice(0, 300);
  const role = e => e.getAttribute('role') || ({BUTTON:'button', A:'link', INPUT: e.type === 'checkbox' ? 'checkbox' : e.type === 'radio' ? 'radio' : e.type === 'search' ? 'searchbox' : 'textbox', TEXTAREA:'textbox', SELECT:'combobox'}[e.tagName]) || (/^H[1-6]$/.test(e.tagName) ? 'heading' : '');
  function pageText() {
    const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
    let node, count = 0, text = '';
    while ((node = walker.nextNode()) && ++count <= 20000 && text.length < 24000) {
      const e = node.parentElement;
      if (!e || e.closest('script,style,noscript,input,textarea,select,[contenteditable],form,[data-private],[data-sensitive]') || !visible(e) || protectedElement(e)) continue;
      const value = node.textContent.trim(); if (value) text += value + '\n';
    }
    return {text: text.slice(0,24000), truncated: text.length >= 24000 || count > 20000};
  }
  function inspect() {
    refs.clear(); const elements = [];
    if (!pageProtected()) {
      const candidates = document.querySelectorAll('button,a[href],input,textarea,select,[role],h1,h2,h3,h4,h5,h6');
      for (const e of [...candidates].slice(0, 5000)) {
        if (elements.length >= 200) break;
        const kind = role(e), label = name(e);
        if (!['button','link','textbox','searchbox','combobox','checkbox','radio','tab','menuitem','heading'].includes(kind) || !visible(e) || protectedElement(e) || e.disabled || e.getAttribute('aria-disabled') === 'true') continue;
        const ref = crypto.randomUUID(); refs.set(ref, {element: e, signature: signature(e), expires: performance.now() + 180000, url: location.href});
        elements.push({ref, role: kind, name: label});
      }
    }
    return {ok: true, document: documentId, title: document.title.slice(0, 300), url: location.href,
      elements, ...(pageProtected() ? {text: '', blocked: true} : pageText())};
  }
  function act(args) {
    const target = refs.get(args.ref);
    if (args.document !== documentId || !target || target.expires < performance.now() || target.url !== location.href) throw Error('Stale document reference.');
    const e = target.element;
    if (!visible(e) || protectedElement(e) || e.disabled || e.getAttribute('aria-disabled') === 'true' || signature(e) !== target.signature) throw Error('Protected or changed control.');
    if (args.action === 'focus') { e.focus(); return {performed: true, verified: document.activeElement === e}; }
    if (args.action === 'click') { e.focus(); e.click(); refs.delete(args.ref); return {performed: true, verified: false, note: 'Click dispatched; verify resulting page state.'}; }
    if (args.action !== 'type' || typeof args.text !== 'string' || args.text.length > 10000 || !['INPUT','TEXTAREA'].includes(e.tagName) || e.readOnly || (e.tagName === 'INPUT' && !['text','search','url','email','tel'].includes(e.type))) throw Error('Choose an editable non-sensitive field.');
    const proto = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    e.focus(); Object.getOwnPropertyDescriptor(proto, 'value').set.call(e, args.text);
    e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true}));
    refs.delete(args.ref); return {performed: true, verified: e.value === args.text};
  }
  function selection() {
    if (pageProtected()) return {text: '', blocked: true};
    const active = document.activeElement, selected = getSelection();
    if (active && (active.matches('input,textarea,[contenteditable]') || protectedElement(active))) return {text: '', blocked: true};
    if (!selected || !selected.rangeCount) return {text: ''};
    const root = selected.getRangeAt(0).commonAncestorContainer;
    const e = root.nodeType === Node.ELEMENT_NODE ? root : root.parentElement;
    if (!e || e.closest('form,[data-private],[data-sensitive]') || protectedElement(e) || e.querySelector('[data-private],[data-sensitive],input[type=password]')) return {text: '', blocked: true};
    return {text: selected.toString().slice(0, 16000)};
  }
  globalThis.__jarvixPage = Object.freeze({dispatch(operation, args) {
    if (operation === 'inspect') return inspect();
    if (operation === 'act') return act(args);
    if (operation === 'selection') return selection();
    if (operation === 'scroll') {
      if (pageProtected() || !['up', 'down'].includes(args.direction) || !Number.isInteger(args.amount) || args.amount < 1 || args.amount > 2000) throw Error('Invalid scroll.');
      const before = scrollY; scrollBy({top: args.amount * (args.direction === 'up' ? -1 : 1), behavior: 'instant'});
      return {changed: before !== scrollY, verified: true};
    }
    throw Error('Unsupported operation.');
  }});
})();

