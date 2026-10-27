// Minimal DOM builder and in-place patcher.
//
// Views build a fresh detached tree with h()/s() on every render; patch()
// then morphs the live DOM to match it. Elements that survive keep focus,
// scroll position, hover and text selection, so a background refresh never
// disturbs the user. Styles are applied through CSSOM, which keeps the page
// compatible with a strict Content-Security-Policy.

const SVG_NS = 'http://www.w3.org/2000/svg';
const HANDLERS = Symbol('handlers');
const TYPES = Symbol('types');
const KEY = Symbol('key');
const VALUE = Symbol('value');
const CHECKED = Symbol('checked');

function dispatch(event) {
  const handler = this[HANDLERS]?.[event.type];
  if (handler) handler.call(this, event);
}

function listen(el, type) {
  if (!el[TYPES]) el[TYPES] = new Set();
  if (el[TYPES].has(type)) return;
  el[TYPES].add(type);
  el.addEventListener(type, dispatch);
}

function className(value) {
  if (!value) return '';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(className).filter(Boolean).join(' ');
  return Object.keys(value).filter(key => value[key]).join(' ');
}

function append(el, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false || child === true) continue;
    if (Array.isArray(child)) append(el, child);
    else if (child instanceof Node) el.append(child);
    else el.append(document.createTextNode(String(child)));
  }
}

function apply(el, props, svg) {
  for (const [name, value] of Object.entries(props)) {
    if (name === 'key') { if (value !== null && value !== undefined) el[KEY] = String(value); continue; }
    if (name === 'class') { const cls = className(value); if (cls) el.setAttribute('class', cls); continue; }
    if (name === 'style') {
      if (value && typeof value === 'object') {
        for (const [prop, val] of Object.entries(value)) {
          if (val !== null && val !== undefined && val !== false) el.style.setProperty(prop, String(val));
        }
      }
      continue;
    }
    if (name.startsWith('on') && typeof value === 'function') {
      const type = name.slice(2).toLowerCase();
      (el[HANDLERS] ||= {})[type] = value;
      listen(el, type);
      continue;
    }
    if (!svg && name === 'value') { el[VALUE] = value === null || value === undefined ? '' : String(value); continue; }
    if (!svg && name === 'checked') { el[CHECKED] = !!value; el.checked = !!value; continue; }
    if (value === false || value === null || value === undefined) continue;
    el.setAttribute(name, value === true ? '' : String(value));
  }
}

/** Create an HTML element. */
export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  // Detached <img> elements start downloading as soon as src is set, so the
  // real src is assigned only once the image is in the live document.
  if (tag === 'img' && props?.src) props = { ...props, src: undefined, 'data-src': props.src };
  if (props) apply(el, props, false);
  append(el, children);
  if (VALUE in el) el.value = el[VALUE];
  return el;
}

/** Create an SVG element. */
export function s(tag, props, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  if (props) apply(el, props, true);
  append(el, children);
  return el;
}

export function text(value) { return document.createTextNode(String(value ?? '')); }

function syncAttributes(old, next) {
  const skipOpen = old.localName === 'dialog' || (old.localName === 'details' && old.hasAttribute('data-keep-open'));
  const lazyImage = old.localName === 'img' && next.hasAttribute('data-src');
  for (const attr of Array.from(old.attributes)) {
    if (attr.name === 'open' && skipOpen) continue;
    if (attr.name === 'src' && lazyImage) continue;
    if (!next.hasAttribute(attr.name)) {
      if (attr.name === 'style') old.style.cssText = '';
      else old.removeAttribute(attr.name);
    }
  }
  for (const attr of Array.from(next.attributes)) {
    if (attr.name === 'open' && skipOpen) continue;
    if (attr.name === 'style') {
      if (old.style.cssText !== next.style.cssText) old.style.cssText = next.style.cssText;
    } else if (old.getAttribute(attr.name) !== attr.value) {
      old.setAttribute(attr.name, attr.value);
    }
  }
}

function patchNode(old, next) {
  if (old === next) return old;
  if (old.nodeType !== next.nodeType || old.nodeName !== next.nodeName || old[KEY] !== next[KEY]) return next;
  if (old.nodeType !== Node.ELEMENT_NODE) {
    if (old.nodeValue !== next.nodeValue) old.nodeValue = next.nodeValue;
    return old;
  }
  const sig = next.getAttribute('data-sig');
  if (sig !== null && old.getAttribute('data-sig') === sig) return old;
  syncAttributes(old, next);
  old[HANDLERS] = next[HANDLERS];
  if (next[TYPES]) for (const type of next[TYPES]) listen(old, type);
  patchChildren(old, Array.from(next.childNodes));
  if (VALUE in next) {
    old[VALUE] = next[VALUE];
    if (old.value !== next[VALUE]) old.value = next[VALUE];
  }
  if (CHECKED in next) {
    old[CHECKED] = next[CHECKED];
    if (old.checked !== next[CHECKED]) old.checked = next[CHECKED];
  }
  return old;
}

function patchChildren(parent, nextNodes) {
  const oldNodes = Array.from(parent.childNodes);
  const keyed = new Map();
  const loose = [];
  for (const node of oldNodes) {
    if (node[KEY] !== undefined) keyed.set(node[KEY], node);
    else loose.push(node);
  }
  let cursor = 0;
  const result = nextNodes.map(next => {
    let match;
    if (next[KEY] !== undefined) {
      match = keyed.get(next[KEY]);
      if (match) keyed.delete(next[KEY]);
    } else {
      match = loose[cursor++];
    }
    return match ? patchNode(match, next) : next;
  });
  result.forEach((node, index) => {
    const current = parent.childNodes[index];
    if (current !== node) parent.insertBefore(node, current || null);
  });
  while (parent.childNodes.length > result.length) parent.removeChild(parent.lastChild);
}

/** Make the children of `root` match `children` (a node or array of nodes). */
export function patch(root, children) {
  const list = [];
  append({ append: node => list.push(node) }, [children]);
  patchChildren(root, list);
  for (const image of root.querySelectorAll('img[data-src]')) {
    if (image.getAttribute('src') !== image.dataset.src) image.src = image.dataset.src;
  }
}
