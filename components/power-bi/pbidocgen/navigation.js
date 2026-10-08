/* Object identity, the finder and navigation between objects.

   The search index is built when the document is generated (object_index.py) and embedded as DERIVED.search:
   one entry per named object with its id, kind, name and parent. Selecting an entry opens the view the object
   lives in, shows it whatever filter or page selection would hide it, scrolls to it, highlights it and moves
   keyboard focus there. An object link (#o/<id>) does the same after a reload and with Back and Forward. */
const SEARCH_INDEX = (typeof DERIVED !== 'undefined' && DERIVED && DERIVED.search) || {kinds:[], parents:[], items:[]};
const KIND_PLURAL = {'data source':'Data sources','query':'Queries','table':'Tables','calculated table':'Calculated tables',
  'calculation group':'Calculation groups','column':'Columns','calculated column':'Calculated columns','measure':'Measures',
  'security role':'Security roles','page':'Pages','visual':'Visuals'};
const columnAnchor = (table, column) => 'col-' + slug(table) + '-' + slug(column);
const visualAnchor = (pageId, visualId) => 'vis-' + pageKey(pageId) + '-' + slug(visualId);
const sourceAnchor = key => 'src-' + slug(key);

const OBJECTS = SEARCH_INDEX.items.map(([kind, name, parent, id, a, b], order) =>
  ({id, kind: SEARCH_INDEX.kinds[kind], name, parent: parent < 0 ? '' : SEARCH_INDEX.parents[parent], a, b, order}));
// Names the page gives an object itself: an untitled visual ("Card · Amount") and an external source.
for (const o of OBJECTS) {
  if (o.kind === 'data source' && Array.isArray(o.a)) {
    const [sourceType, server, database, schema, object, location] = o.a;
    o.key = JSON.stringify(o.a);
    o.name = sourceName({sourceType, server, database, schema, object, location});
  } else if (o.kind === 'visual' && o.name == null) {
    const page = R?.pages.find(p => p.id === o.a), visual = page?.visuals.find(v => v.id === o.b);
    o.name = visual ? visualName(o.a, visual) : String(o.b || 'Visual');
  }
  o.name = String(o.name ?? '');
  o.lower = o.name.toLowerCase();
  o.qualified = (o.parent ? o.parent + '[' + o.name + ']' : o.name).toLowerCase();
}
const OBJECT_BY_ID = new Map(OBJECTS.map(o => [o.id, o]));
// How the rest of the page names an object: by what it is and the name or ids it already holds.
const refKey = (family, ...parts) => family + '\u0001' + parts.join('\u0001');
const OBJECT_BY_REF = new Map();
for (const o of OBJECTS) {
  const keys = {
    'table': () => [refKey('table', o.name)], 'calculated table': () => [refKey('table', o.name)],
    'calculation group': () => [refKey('table', o.name)],
    'column': () => [refKey('column', o.parent, o.name)], 'calculated column': () => [refKey('column', o.parent, o.name)],
    'measure': () => [refKey('measure', o.name), refKey('measure', o.parent, o.name)],
    'query': () => [refKey('query', o.name)], 'security role': () => [refKey('security role', o.name)],
    'page': () => [refKey('page', o.a)], 'visual': () => [refKey('visual', o.a, o.b)],
    'data source': () => [refKey('data source', o.key || o.a)],
  }[o.kind];
  for (const key of keys ? keys() : []) if (!OBJECT_BY_REF.has(key)) OBJECT_BY_REF.set(key, o);
}
const objectFor = (family, ...parts) => OBJECT_BY_REF.get(refKey(family, ...parts));

/* ------------------------------------------------------------ where an object lives */
// tab: the view that shows it. el: its element there. then: what to do once it is on screen.
function homeOf(o) {
  switch (o.kind) {
    case 'table': case 'calculated table': case 'calculation group':
      return {tab: 'tables', el: 'tbl-' + slug(o.name)};
    case 'column': case 'calculated column':
      return {tab: 'tables', el: columnAnchor(o.parent, o.name)};
    case 'measure': return {tab: 'measures', el: 'mea-' + slug(o.name)};
    // The query is picked in the queries pane; what the reader is taken to is the query itself, beside it.
    case 'query': return {tab: 'power-query', el: pqItemId(o.id), then: () => selectQuery(o.id, false), show: 'pq-title'};
    case 'security role': return {tab: 'security', el: 'role-' + slug(o.name)};
    case 'page': return {tab: 'pages', el: 'pg-' + pageKey(o.a)};
    case 'visual': return {tab: 'pages', el: visualAnchor(o.a, o.b)};
    case 'data source':
      return o.a === 'live' ? {tab: 'sources', el: 'src-live'}
        : {tab: 'sources', el: sourceAnchor(o.key), then: () => inspectSource(o.key)};
  }
  return null;
}
const nextFrame = fn => (typeof requestAnimationFrame === 'function' ? requestAnimationFrame(fn) : fn());
function openContainers(el) {
  for (let node = el; node; node = node.parentElement) if (node.tagName === 'DETAILS') node.open = true;
}
// Laid out and not hidden by a filter (display:none) or a closed container.
const onScreen = el => !!el && (typeof el.getClientRects !== 'function' || el.getClientRects().length > 0);
// A search box holding text, or a drop-down moved off its first option, in the view now shown.
function viewFiltered() {
  return Array.from(document.querySelectorAll('#main input[id]:not([type=file]):not([type=checkbox]), #main select[id]'))
    .some(el => el.id !== 'global-page' && (el.tagName === 'SELECT' ? el.selectedIndex > 0 : !!el.value));
}
let objectMarkTimer = null;
function markObject(el) {
  if (!el.classList || !el.classList.contains) return;
  document.querySelectorAll('.obj-hit').forEach(other => other.classList.remove('obj-hit'));
  el.classList.add('obj-hit');
  clearTimeout(objectMarkTimer);
  objectMarkTimer = setTimeout(() => el.classList.remove('obj-hit'), 4000);
}
function focusObject(el) {
  const target = el.tagName === 'DETAILS' ? (el.querySelector(':scope > summary') || el) : el;
  if (target.tagName !== 'SUMMARY' && target.tagName !== 'BUTTON' && target.tagName !== 'A' &&
      target.getAttribute && target.getAttribute('tabindex') === null) target.setAttribute('tabindex', '-1');
  if (target.focus) target.focus({preventScroll: true});
}
function noteNavigation(text) {
  const note = document.getElementById('finder-note');
  if (!note) return;
  note.textContent = text;
  clearTimeout(noteNavigation.timer);
  if (text) noteNavigation.timer = setTimeout(() => { note.textContent = ''; }, 8000);
}
function objectHash(id) { return '#o/' + encodeURIComponent(id); }
function recordObject(id) {
  if (!window.location || !window.history || window.location.hash === objectHash(id)) return;
  // Framed (library viewer, portable export): never add entries to the host page's history.
  if (window.location.hash && window.parent === window) window.history.pushState(null, '', objectHash(id));
  else window.history.replaceState(null, '', objectHash(id));
}
/* Show an object: open its view, undo whatever hides it, then scroll, highlight and focus.
   Returns false when the object is not in this document or its view is not available. */
function goObject(id, {history = true} = {}) {
  const o = OBJECT_BY_ID.get(id), home = o && homeOf(o);
  if (!home || !TABS.some(t => t.id === home.tab && t.avail)) return false;
  const find = () => { const el = document.getElementById(home.el); if (el) openContainers(el); return el; };
  if (activeTab !== home.tab) switchTab(home.tab, false);
  let el = find();
  const undone = [];
  if (!onScreen(el) && viewFiltered()) {       // a search box or drop-down in this view hides it
    switchTab(home.tab, false, true);
    el = find();
    undone.push('the filter in this view');
  }
  if (!onScreen(el) && pageScope !== '*') {    // so does the report page selection
    pageScope = '*';
    switchTab(home.tab, false, true);
    el = find();
    undone.push('the report page selection');
  }
  if (!el) return false;
  if (el.tagName === 'DETAILS') el.open = true;
  if (home.then) home.then(el);
  if (home.show) el = document.getElementById(home.show) || el;
  if (history) recordObject(id);
  document.title = o.name + ' · ' + DATA.title;
  noteNavigation(onScreen(el) && undone.length ? `Cleared ${undone.join(' and ')} to show ${o.name}.` : '');
  nextFrame(() => {
    // Straight there: an animated scroll across a long view leaves the reader waiting for the object.
    // Something taller than the window is shown from its top (below the bars that stay on screen), not its middle.
    const tall = !!el.getBoundingClientRect && el.getBoundingClientRect().height > (window.innerHeight || 0) * 0.7;
    if (tall && el.style) el.style.scrollMarginTop = '124px';
    if (el.scrollIntoView) el.scrollIntoView({block: tall ? 'start' : 'center', behavior: 'instant'});
    markObject(el);
    focusObject(el);
  });
  return true;
}
function goRef(family, ...rest) {
  const options = rest.length && rest[rest.length - 1] && typeof rest[rest.length - 1] === 'object' ? rest.pop() : undefined;
  const o = objectFor(family, ...rest);
  return !!o && goObject(o.id, options);
}
// Tab ids that an older link may still carry.
const TAB_ALIASES = {};
function routeReport() {
  const raw = (window.location && window.location.hash || '').slice(1);
  if (raw.startsWith('o/')) {
    let id = '';
    try { id = decodeURIComponent(raw.slice(2)); } catch (error) { id = ''; }
    if (goObject(id, {history: false})) return;
    switchTab('overview', false);
    noteNavigation('That link points to an object this document does not contain. It may have been removed or renamed since the link was made.');
    return;
  }
  const wanted = TAB_ALIASES[raw] || raw;
  const tab = TABS.find(t => t.id === wanted && t.avail);
  switchTab(tab ? tab.id : 'overview', false);
}

/* ------------------------------------------------------------ the finder */
/* Objects whose name contains the text, best match first within each kind: the whole name, its start, the
   start of a word, anywhere, then the qualified name (Table[Column]). With no text, every object. */
function searchObjects(text) {
  const q = String(text || '').trim().toLowerCase();
  const byKind = new Map();
  let total = 0;
  for (const o of OBJECTS) {
    let rank = 0;
    if (q) {
      const at = o.lower.indexOf(q);
      if (at === 0) rank = o.lower.length === q.length ? 0 : 1;
      else if (at > 0) rank = /[^\p{L}\p{N}]/u.test(o.lower[at - 1]) ? 2 : 3;
      else if (o.qualified.includes(q)) rank = 4;
      else continue;
    }
    if (!byKind.has(o.kind)) byKind.set(o.kind, []);
    byKind.get(o.kind).push({o, rank});
    total++;
  }
  const groups = SEARCH_INDEX.kinds.filter(kind => byKind.has(kind)).map(kind => {
    const hits = byKind.get(kind);
    if (q) hits.sort((x, y) => x.rank - y.rank || x.o.order - y.o.order);
    return {kind, label: KIND_PLURAL[kind] || kind, items: hits.map(h => h.o)};
  });
  return {query: q, total, groups};
}
const FINDER_GROUP_SIZE = 6, FINDER_GROUP_MAX = 400;
const finder = {open: false, active: -1, options: [], expanded: new Set(), query: null};
function buildFinder() {
  const host = document.getElementById('finder');
  if (!host) return;
  host.innerHTML = `<div class="finder-box" id="finder-box">
    <label class="finder-label" for="finder-input">Find</label>
    <div class="finder-field">
      <input id="finder-input" class="finder-input" type="text" role="combobox" autocomplete="off" spellcheck="false"
        aria-expanded="false" aria-controls="finder-list" aria-autocomplete="list" aria-haspopup="listbox"
        placeholder="Table, column, measure, query, page, visual or source…"
        oninput="finderInput()" onfocus="finderOpen()" onclick="finderOpen()" onkeydown="finderKey(event)" onblur="finderClose()">
      <button type="button" id="finder-clear" class="finder-clear" aria-label="Clear search" title="Clear" onmousedown="event.preventDefault()" onclick="finderClear()" hidden>×</button>
      <div id="finder-panel" class="finder-panel" onmousedown="event.preventDefault()" hidden><div id="finder-list" role="listbox" aria-label="Objects in this document"></div></div>
    </div>
    <span id="finder-count" class="finder-count" role="status" aria-live="polite"></span>
    <span class="finder-hint" aria-hidden="true">Press <kbd>/</kbd> to search</span>
  </div>
  <p id="finder-note" class="finder-note" role="status" aria-live="polite"></p>`;
  renderFinder();
}
const markMatch = (name, q) => {
  const at = q ? name.toLowerCase().indexOf(q) : -1;
  return at < 0 ? esc(name) : esc(name.slice(0, at)) + '<mark>' + esc(name.slice(at, at + q.length)) + '</mark>' + esc(name.slice(at + q.length));
};
function renderFinder() {
  const input = document.getElementById('finder-input'), list = document.getElementById('finder-list');
  if (!input || !list) return;
  const found = searchObjects(input.value);
  if (found.query !== finder.query) { finder.query = found.query; finder.expanded.clear(); finder.active = -1; }
  finder.options = [];
  let html = '';
  for (const group of found.groups) {
    const all = finder.expanded.has(group.kind) || group.items.length <= FINDER_GROUP_SIZE + 1;
    const items = group.items.slice(0, all ? FINDER_GROUP_MAX : FINDER_GROUP_SIZE);
    html += `<div class="finder-group" role="group" aria-label="${esc(group.label)}"><div class="finder-kind" role="presentation">${esc(group.label)} <span class="n">${num(group.items.length)}</span></div>`;
    for (const o of items) {
      const i = finder.options.push({id: o.id}) - 1;
      html += `<div class="finder-opt" role="option" id="finder-opt-${i}" aria-selected="false" data-object="${esc(o.id)}"
        aria-label="${esc(o.name + ', ' + o.kind + (o.parent ? ', ' + o.parent : ''))}"
        onclick="finderActivate(${i})"><span class="fo-name">${markMatch(o.name, found.query)}</span><span class="fo-kind">${esc(o.kind)}</span><span class="fo-parent">${esc(o.parent)}</span></div>`;
    }
    if (items.length < group.items.length && all) {
      html += `<div class="finder-more" role="presentation">Type more to narrow the other ${num(group.items.length - items.length)}</div>`;
    } else if (items.length < group.items.length) {
      const i = finder.options.push({expand: group.kind}) - 1;
      html += `<div class="finder-opt finder-more" role="option" id="finder-opt-${i}" aria-selected="false" onclick="finderActivate(${i})">Show all ${num(group.items.length)} ${esc(group.label.toLowerCase())}</div>`;
    }
    html += '</div>';
  }
  if (!found.total) html = `<div class="finder-empty">No objects match “${esc(input.value.trim())}”. Check the spelling, or clear the box to browse everything.</div>`;
  list.innerHTML = html;
  const count = document.getElementById('finder-count'), clear = document.getElementById('finder-clear');
  if (count) count.textContent = found.query ? (found.total ? plural(found.total, 'result') : 'No results') : plural(found.total, 'object');
  if (clear) clear.hidden = !input.value;
  // Typing puts the best match under Enter; browsing leaves the choice to the arrow keys.
  if (found.query && found.total && finder.active < 0) finder.active = 0;
  if (finder.active >= finder.options.length) finder.active = finder.options.length - 1;
  finderMark();
}
function finderMark() {
  const input = document.getElementById('finder-input');
  finder.options.forEach((_, i) => {
    const el = document.getElementById('finder-opt-' + i);
    if (!el) return;
    el.setAttribute('aria-selected', String(i === finder.active));
    if (el.classList.toggle) el.classList.toggle('active', i === finder.active);
    if (i === finder.active && finder.open && el.scrollIntoView) el.scrollIntoView({block: 'nearest'});
  });
  if (input) {
    if (finder.open && finder.active >= 0) input.setAttribute('aria-activedescendant', 'finder-opt-' + finder.active);
    else if (input.removeAttribute) input.removeAttribute('aria-activedescendant');
  }
}
function finderShow(open) {
  const panel = document.getElementById('finder-panel'), input = document.getElementById('finder-input');
  finder.open = open;
  if (panel) panel.hidden = !open;
  if (input) input.setAttribute('aria-expanded', String(open));
  finderMark();
}
function finderOpen() { if (!finder.open) { renderFinder(); finderShow(true); } }
function finderClose() { if (finder.open) finderShow(false); }
// Typing narrows this list only. A view's own search box is never touched from here.
function finderInput() { renderFinder(); finderShow(true); }
function finderClear() {
  const input = document.getElementById('finder-input');
  if (!input) return;
  input.value = '';
  renderFinder();
  finderShow(true);
  if (input.focus) input.focus();
}
function finderActivate(i) {
  const option = finder.options[i];
  if (!option) return;
  if (option.expand) { finder.expanded.add(option.expand); finder.active = i; renderFinder(); return; }
  finderClose();
  goObject(option.id);
}
function finderKey(event) {
  const input = document.getElementById('finder-input');
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    if (!finder.open) { finderOpen(); if (finder.active >= 0) return; }
    const n = finder.options.length;
    if (n) finder.active = event.key === 'ArrowDown' ? (finder.active + 1) % n : (finder.active <= 0 ? n - 1 : finder.active - 1);
    finderMark();
  } else if (event.key === 'Enter') {
    if (finder.open && finder.active >= 0) { event.preventDefault(); finderActivate(finder.active); }
  } else if (event.key === 'Escape') {
    if (finder.open) { event.preventDefault(); finderClose(); }
    else if (input && input.value) { event.preventDefault(); finderClear(); finderClose(); }
  }
}
if (typeof document !== 'undefined' && document.addEventListener) {
  // "/" from anywhere that is not a text field moves to the finder.
  document.addEventListener('keydown', event => {
    if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) return;
    const t = event.target, tag = t && t.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (t && t.isContentEditable)) return;
    const input = document.getElementById('finder-input');
    if (input) { event.preventDefault(); input.focus(); if (input.select) input.select(); }
  });
}
