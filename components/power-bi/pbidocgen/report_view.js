/* Report view (UI rework, Change 4: Report view).

   Pages: one page at a time, in report order, as Power BI shows them. The page list names every page and says
   which are hidden, tooltip or drillthrough pages. The page's layout is on top, drawn from saved visual
   positions, with a panel beside it: select a visual for its fields and the filters on it, grouped as Power BI's
   Filters pane groups them (on this visual, on this page, on all pages). Below the layout, every visual of the
   page with its fields, for scanning and printing, and what feeds the page.
   Visuals and Filters are flat lists of the whole report, for searching. Bookmarks lists every bookmark read.

   The page type comes from the page settings in the file (page_types.py). A page whose settings the document does
   not hold says "page type not recorded"; it is never shown as an ordinary page. */
let reportPageSel = '';      // the page shown in Pages ('' until the reader picks one)
let reportVisualSel = '';    // the visual selected on that page ('' for none)

/* ------------------------------------------------------------ page types */
const PAGE_TYPES = {
  page: {label: 'Report page', badge: '', cls: ''},
  tooltip: {label: 'Tooltip page', badge: 'tooltip page', cls: 'pt-tooltip'},
  drillthrough: {label: 'Drillthrough page', badge: 'drillthrough page', cls: 'pt-drill'},
};
function pageType(p){
  if(PAGE_TYPES[p.pageType]) return {kind: p.pageType, ...PAGE_TYPES[p.pageType]};
  if(p.pageType === 'other') return {kind: 'other', label: `Page type not recognised (${p.pageTypeRaw || 'no value'})`, badge: 'page type not recognised', cls: 'pt-unknown'};
  return {kind: 'not-recorded', label: 'Page type not recorded', badge: 'page type not recorded', cls: 'pt-unknown'};
}
// Everything that sets a page apart, as badges: its type (unless it is an ordinary page), hidden, landing page.
function pageBadges(p, {landing = true} = {}){
  const t = pageType(p);
  return [t.badge ? `<span class="badge pt ${t.cls}">${esc(t.badge)}</span>` : '',
    p.hidden ? '<span class="badge b-hidden">hidden</span>' : '',
    landing && p.isActive ? '<span class="badge b-other">landing page</span>' : ''].filter(Boolean).join(' ');
}
// The same, as text where markup cannot go (a drop-down): only what sets the page apart, and only when recorded.
const pageFlagsText = p => { const t = pageType(p), f = [t.badge, p.hidden ? 'hidden' : ''].filter(Boolean); return f.length ? ` (${f.join(', ')})` : ''; };
// Its drillthrough fields: on a tooltip page these are its tooltip fields.
const drillFields = p => (p.filters || []).filter(f => f.drillthrough && (f.level || 'page') === 'page');
const fieldRef = f => `${f.table && has.model ? tblLink(f.table) : `<span class="ref">${esc(f.table || '?')}</span>`}<span class="ref">[${esc(f.field || '?')}]</span>${fx(f.table, f.field)}`;
function pageTypeNote(p){
  const t = pageType(p), fields = drillFields(p), list = fields.map(fieldRef).join(', ');
  const notes = {
    tooltip: `A tooltip page: Power BI shows it as the tooltip of visuals${fields.length ? ` that use its tooltip fields: ${list}` : ''}.`,
    drillthrough: `A drillthrough page: readers reach it by drilling through from another page${fields.length ? ` on ${list}` : ''}.`,
    other: `The page settings give a page type this document does not recognise (${esc(p.pageTypeRaw || 'no value')}).`,
    'not-recorded': 'Page type not recorded: this document does not say whether it is an ordinary page, a tooltip page or a drillthrough page.',
  };
  const hidden = p.hidden ? ' Hidden: readers of the published report do not see it in the page list.' : '';
  return notes[t.kind] || hidden ? `<p class="page-type-note" id="page-type-note">${notes[t.kind] || ''}${hidden}</p>` : '';
}

/* ------------------------------------------------------------ which page */
function shownPage(){
  const pages = R?.pages || [];
  return pages.find(p => p.id === reportPageSel) || pages.find(p => p.id === pageScope) || pages[0];
}
// Set the page (and visual) the view shows, without drawing it: navigation draws once.
function setReportPage(pageId, visualId = ''){ reportPageSel = pageId; reportVisualSel = visualId || ''; }
// Choosing a page is a step in history (Back and Forward move between pages) and its link opens it after a reload.
function showReportPage(pageId){
  setReportPage(pageId);
  const o = objectFor('page', pageId);
  switchTab('pages', !o);
  if(o) recordObject(o.id);
  // The page list is drawn by now: focus stays on it at once, so a keyboard reader can keep moving through pages.
  document.getElementById('page-strip')?.querySelector?.('[aria-current="page"]')?.focus?.({preventScroll: true});
}
// The address names the page and the selected visual, so a reload or a copied link opens the same; selecting a
// visual replaces the entry rather than adding one.
function noteReportPlace(){
  const p = shownPage();
  if(!p || !window.location || !window.history) return;
  const o = (reportVisualSel && objectFor('visual', p.id, reportVisualSel)) || objectFor('page', p.id);
  if(o && window.location.hash !== objectHash(o.id)) window.history.replaceState(null, '', objectHash(o.id));
}
// The Report page selection of the usage views follows into Pages, so a reader who chose a page sees it there.
function notePageScope(id){ if((R?.pages || []).some(p => p.id === id) && id !== reportPageSel) setReportPage(id); }
const visualBoxId = (pageId, visualId) => 'vbox-' + pageKey(pageId) + '-' + slug(visualId);

/* ------------------------------------------------------------ Pages */
function rPages(){
  if(!R.pages.length) return `${coupler()}<h1>Pages</h1><div class="empty"><b>No pages found.</b><br>Check that the .Report folder contains definition/pages/.</div>`;
  const p = shownPage();
  if(reportVisualSel && !p.visuals.some(v => v.id === reportVisualSel)) reportVisualSel = '';
  const strip = R.pages.map((q, i) => {
    const t = pageType(q), flags = [t.badge, q.hidden ? 'hidden' : ''].filter(Boolean);
    return `<button type="button" class="page-tab${q === p ? ' is-current' : ''}${q.hidden ? ' is-hidden' : ''}" data-page="${esc(q.id)}"${q === p ? ' aria-current="page"' : ''} title="${esc(q.id)}" onclick="${action('showReportPage', q.id)}">
      <span class="page-no">${i + 1}</span><span class="page-name">${esc(pageTitle(q))}</span>${flags.length ? `<span class="page-flags">${flags.map(esc).join(' · ')}</span>` : ''}</button>`;
  }).join('');
  const g = layoutGeometry(p), deco = p.visuals.filter(isDecorative), data = p.visuals.filter(v => !isDecorative(v));
  const t = pageType(p);
  const facts = [t.label, p.width && p.height ? `${num(p.width)} × ${num(p.height)}` : '',
    `${plural(data.length, 'data visual')}${deco.length ? ` · ${deco.length} decorative` : ''}`].filter(Boolean);
  const legend = Object.entries(KIND_CLASS).map(([k, c]) => `<span><span class="dot ${c}"></span>${esc(k)}</span>`).join('');
  return `${coupler()}<h1>Pages</h1>
  <p class="sub">One page at a time, in report order. The layout is drawn from saved visual positions, not rendered charts. Select a visual for its fields and its filters, grouped as the Filters pane groups them.${has.model ? '' : ' Field references are unresolved (report-only mode): they name what the report expects, not what a model confirms.'}</p>
  <div class="page-strip" id="page-strip" role="navigation" aria-label="Report pages, in report order">${strip}</div>
  <section class="page-view" id="pg-${pageKey(p.id)}" data-page="${esc(p.id)}" aria-labelledby="page-title">
    <div class="page-head"><h2 id="page-title" title="${esc(p.id)}">${esc(pageTitle(p))}</h2> ${pageBadges(p)}</div>
    <p class="page-facts mut">${facts.map(esc).join(' · ')}</p>
    ${pageTypeNote(p)}
    <div class="card page-surface" id="page-surface" onkeydown="pageSurfaceKey(event)">
      <div class="page-main">
        ${g.placed.length ? `<div class="erd-toolbar layout-toolbar"><span class="mut">Select a visual for its fields and filters.</span><div class="erd-zoom" role="group" aria-label="Zoom ${esc(p.name)}"><button type="button" aria-label="Zoom out ${esc(p.name)}" onclick="${action('zoomPageLayout', p.id, -.25)}">−</button><output id="layout-zoom-${pageKey(p.id)}" aria-live="polite">${Math.round((layoutZooms.get(p.id) || 1) * 100)}%</output><button type="button" aria-label="Zoom in ${esc(p.name)}" onclick="${action('zoomPageLayout', p.id, .25)}">+</button><button type="button" onclick="${action('zoomPageLayout', p.id, 0)}">Fit width</button></div></div>
        <div class="layout-viewport page-viewport" id="page-viewport" tabindex="0" role="region" aria-label="${esc(p.name)} visual layout" onclick="pageCanvasClick(event)"><div id="layout-canvas-${pageKey(p.id)}" class="page-canvas" style="width:${(layoutZooms.get(p.id) || 1) * 100}%;aspect-ratio:${g.width}/${g.height}">${g.placed.map(v => visualBox(p, v, g)).join('')}</div></div>
        <div class="legend layout-legend">${legend}<span><span class="dot" style="border:1px dashed var(--ink3);background:transparent"></span>Hidden</span><span><span class="badge b-warn">!</span> Unresolved binding</span></div>`
        : `<p class="mut page-no-layout">${p.visuals.length ? 'No visual on this page has usable coordinates, so there is no layout to draw. Every visual is listed below.' : 'This page has no visuals.'}</p>`}
        ${g.unplaced.length && g.placed.length ? `<p class="mut page-unplaced">${plural(g.unplaced.length, 'visual')} without usable coordinates ${g.unplaced.length === 1 ? 'is' : 'are'} listed below, not drawn.</p>` : ''}
      </div>
      <aside class="page-panel" id="page-panel" aria-live="polite" aria-label="Fields and filters of the selection">${pagePanel(p)}</aside>
    </div>
    <h3>Visuals on this page</h3>
    <div class="column-scroll"><table class="t page-visuals" id="page-visuals"><thead><tr><th>Visual</th><th>Fields</th><th>Filters on this visual</th></tr></thead>
      <tbody>${data.map(v => visualRow(p, v)).join('') || '<tr><td colspan="3" class="mut">No data visuals on this page.</td></tr>'}</tbody></table></div>
    ${deco.length ? `<details id="page-decorative"><summary>${plural(deco.length, 'decorative visual')} without data <span class="mut" style="font-weight:400">${esc(Object.entries(deco.reduce((a, v) => (a[visualTypeName(v.type)] = (a[visualTypeName(v.type)] || 0) + 1, a), {})).map(([k, n]) => n > 1 ? k + ' ×' + n : k).join(', '))}</span></summary><div class="body"><p class="mut">${deco.map(v => `<button class="xl" id="${visualAnchor(p.id, v.id)}" onclick="${action('pickVisual', p.id, v.id)}">${esc(visualName(p.id, v))}</button>`).join(' · ')}</p></div></details>` : ''}
    <h3>What feeds this page</h3>
    ${(p.feeds || []).length ? `<table class="t page-feeds"><thead><tr><th>Table</th><th>Fields used</th></tr></thead><tbody>${p.feeds.map(({table: tn, fields, usage}) =>
      `<tr><td>${has.model ? tblLink(tn) : `<span class="ref">${esc(tn)}</span>`}<div class="mut">${esc(usage)}</div></td><td class="mut">${fields.map(f => `<span class="ref">${esc(f)}</span>${fx(tn, f)}`).join(' ')}</td></tr>`).join('')}</tbody></table>`
      : '<p class="mut">No field bindings detected on this page.</p>'}
  </section>`;
}
function visualBox(p, v, g){
  const f = visualFields(p.id, v), kind = visualKind(v), selected = v.id === reportVisualSel;
  const names = [...f.measures.map(n => 'Σ ' + n.name), ...f.columns.map(n => n.name + fxText(n.table, n.name))];
  const label = `${visualName(p.id, v)} (${kind}${v.hidden ? ', hidden' : ''})${f.unresolved.length ? `, ${f.unresolved.length} unresolved binding(s)` : ''}`;
  return `<button type="button" id="${visualBoxId(p.id, v.id)}" data-page-id="${esc(p.id)}" data-visual-id="${esc(v.id)}" aria-pressed="${selected}" class="visual-box ${KIND_CLASS[kind]}${v.hidden ? ' hidden-visual' : ''}" style="left:${100 * (v.x - g.left) / g.width}%;top:${100 * (v.y - g.top) / g.height}%;width:${100 * v.width / g.width}%;height:${100 * v.height / g.height}%" title="${esc(label + (names.length ? ': ' + names.join(', ') : ''))}" aria-label="${esc(label)}" onclick="${action('pickVisual', p.id, v.id)}">
    <span class="vb-head">${f.unresolved.length ? '<span class="badge b-warn">!</span> ' : ''}<b>${esc(visualName(p.id, v))}</b><small>${esc(visualTypeName(v.type))}${v.hidden ? ' · hidden' : ''}</small></span>
    <span class="vb-fields">${names.map(esc).join('<br>')}</span></button>`;
}
// A visual's bindings, one per field, in the order the file gives them.
const uniqueFields = v => (v.fields || []).filter((f, i, a) => a.findIndex(g => g.table === f.table && g.field === f.field && g.kind === f.kind) === i);
const fieldChip = f => `<span class="ref" title="${esc(f.context || '')}">${esc(f.table ? `${f.table}[${f.field}]` : `[${f.field}]`)}${f.kind === 'measure' ? ' (m)' : ''}</span>${fx(f.table, f.field)}`;
function visualRow(p, v){
  const filters = (v.filters || []);
  return `<tr id="${visualAnchor(p.id, v.id)}" tabindex="-1"${v.id === reportVisualSel ? ' class="is-selected"' : ''}><td title="${esc(v.type)}"><button class="xl" onclick="${action('pickVisual', p.id, v.id)}">${esc(visualName(p.id, v))}</button>${v.hidden ? ' <span class="badge b-hidden">hidden</span>' : ''}<div class="mut">${esc(visualTypeName(v.type))}</div></td>
    <td class="mut">${uniqueFields(v).map(fieldChip).join(' ') || '—'}</td>
    <td class="mut">${filters.length ? filters.map(f => `${fieldRef(f)}${f.raw ? ` <span>${esc(f.raw)}</span>` : ''}`).join('<br>') : '—'}</td></tr>`;
}

/* ------------------------------------------------------------ the panel: the selected visual and the Filters pane */
function filterItem(f){
  const flags = [f.isHidden ? '<span class="badge b-hidden">hidden from readers</span>' : '', f.isLocked ? '<span class="badge b-other">locked</span>' : ''].filter(Boolean).join(' ');
  return `<li class="fp-item">${f.displayName ? `<b>${esc(f.displayName)}</b> ` : ''}${fieldRef(f)} <span class="mut">${esc(f.filterType || 'basic')}${f.raw ? ': ' + esc(f.raw) : ''}</span>${flags ? ' ' + flags : ''}</li>`;
}
function filterGroup(id, title, filters, empty){
  return `<section class="fp-group" id="${id}"><h4>${esc(title)} <span class="n">${filters.length}</span></h4>${filters.length ? `<ul class="fp-list">${filters.map(filterItem).join('')}</ul>` : `<p class="mut">${esc(empty)}</p>`}</section>`;
}
const pageFilters = p => (p.filters || []).filter(f => (f.level || 'page') === 'page' && !f.drillthrough);
function filtersPane(p, v){
  const visualGroup = v
    ? filterGroup('fp-visual', 'Filters on this visual', v.filters || [], 'None on this visual.')
    : (() => {
        const withFilters = p.visuals.filter(x => (x.filters || []).length);
        return `<section class="fp-group" id="fp-visual"><h4>Filters on this visual</h4>${withFilters.length
          ? `<p class="mut">Select a visual to see its own. ${withFilters.length === 1 ? 'One visual has' : withFilters.length + ' visuals have'} filters on this page:</p><ul class="fp-list">${withFilters.map(x => `<li><button class="xl" onclick="${action('pickVisual', p.id, x.id)}">${esc(visualName(p.id, x))}</button> <span class="mut">${plural(x.filters.length, 'filter')}</span></li>`).join('')}</ul>`
          : '<p class="mut">No visual on this page has a filter of its own.</p>'}</section>`;
      })();
  return `<div class="filters-pane" id="filters-pane"><h3 class="fp-title">Filters</h3>${visualGroup}
    ${filterGroup('fp-page', 'Filters on this page', pageFilters(p), 'None on this page.')}
    ${filterGroup('fp-report', 'Filters on all pages', R.reportFilters || [], 'None on all pages.')}</div>`;
}
function pagePanel(p){
  const v = reportVisualSel && p.visuals.find(x => x.id === reportVisualSel);
  if(!v) return `<p class="mut panel-hint">${!p.visuals.length ? 'This page has no visuals; its filters are below.' : `Select a visual ${layoutGeometry(p).placed.length ? 'in the layout or the list' : 'in the list below'} for its fields and the filters on it.`}</p>${filtersPane(p, null)}`;
  const f = visualFields(p.id, v), roots = (graph.consumers || []).filter(c => c.pageId === p.id && c.visualId === v.id);
  const resolved = [...new Set(roots.map(c => c.node))].map(id => graphNodes.get(id)).filter(Boolean);
  const role = c => !c || /^visual\b|^bookmark$/.test(c) ? '' : c === 'formatting' ? 'formatting rule' : c;
  return `<div class="page-panel-head"><h3 id="panel-visual">${esc(visualName(p.id, v))}</h3><button type="button" class="chip" id="visual-clear" onclick="clearPickedVisual()">Clear selection</button></div>
    <p>${esc(visualTypeName(v.type))} <span class="mut">(${esc(v.type)} · ${esc(v.id)})</span>${v.hidden ? ' <span class="badge b-hidden">hidden</span>' : ''}</p>
    ${f.unresolved.length ? `<p><span class="badge b-warn">Unresolved</span> ${f.unresolved.map(x => esc(`${x.table || '?'}[${x.field}]`)).join(', ')} could not be matched to the model, so usage for that table is uncertain.</p>` : ''}
    <h4>Fields</h4>${uniqueFields(v).length ? `<ul class="fp-list">${uniqueFields(v).map(x => `<li>${fieldChip(x)}${role(x.context) ? ` <span class="mut">${esc(role(x.context))}</span>` : ''}</li>`).join('')}</ul>` : '<p class="mut">No field bindings detected.</p>'}
    ${has.model ? `<h4>In the model</h4>${resolved.length ? `<p>${resolved.map(n => `<button class="xl" onclick="${action('inspectNode', n.id, p.id)}">${esc(n.label)}</button>${n.kind === 'column' ? fx(n.table, n.name) : ''}`).join('<br>')}</p>` : '<p class="mut">No resolved model fields.</p>'}` : ''}
    ${filtersPane(p, v)}`;
}
// Selecting a visual: its box is pressed, its row marked and the panel shows it. Nothing else is redrawn.
function pickVisual(pageId, visualId, {toggle = true, reveal = true} = {}){
  if(activeTab !== 'pages' || shownPage()?.id !== pageId){ setReportPage(pageId, visualId); switchTab('pages'); }
  else reportVisualSel = toggle && reportVisualSel === visualId ? '' : visualId;
  const p = shownPage();
  document.querySelectorAll('#page-surface .visual-box').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.visualId === reportVisualSel)));
  document.querySelectorAll('#page-visuals tr[id]').forEach(r => r.classList.toggle('is-selected', r.id === visualAnchor(p.id, reportVisualSel)));
  const panel = document.getElementById('page-panel'); if(panel) panel.innerHTML = pagePanel(p);
  noteReportPlace();
  if(reveal && reportVisualSel){
    // From the list below or another view, bring the layout and its panel into view; from the layout, it is there.
    const surface = document.getElementById('page-surface'), box = document.getElementById(visualBoxId(p.id, reportVisualSel));
    if(surface && !(surface.contains && surface.contains(document.activeElement))){
      surface.scrollIntoView?.({block: 'nearest', behavior: 'instant'});
      box?.scrollIntoView?.({block: 'nearest', inline: 'nearest', behavior: 'instant'});
      box?.focus?.({preventScroll: true});
    }
  }
}
function clearPickedVisual(){
  const p = shownPage(), was = reportVisualSel;
  if(was) pickVisual(p.id, was, {reveal: false});
  document.getElementById(visualBoxId(p.id, was))?.focus?.({preventScroll: true});
}
function pageSurfaceKey(e){ if(e.key === 'Escape' && reportVisualSel){ e.preventDefault(); clearPickedVisual(); } }
function pageCanvasClick(e){ if(reportVisualSel && (!e.target.closest || !e.target.closest('.visual-box'))) clearPickedVisual(); }
// From anywhere in the document: the visual, on its page, selected (with Back and Forward).
function inspectVisual(pageId, visualId){
  const o = objectFor('visual', pageId, visualId);
  if(o && goObject(o.id)) return;
  setReportPage(pageId, visualId);
  switchTab('pages');
}
const showVisual = inspectVisual;

/* ------------------------------------------------------------ Visuals: every visual, for searching and printing */
let visibleVisuals = [];
function visualRows(query = ''){
  const q = query.trim().toLowerCase();
  return scopedPages().flatMap(p => p.visuals.map(v => ({p, v}))).filter(({p, v}) => !q ||
    [pageTitle(p), visualName(p.id, v), visualTypeName(v.type), v.type, ...(v.fields || []).map(f => `${f.table || ''}[${f.field}]`)].join(' ').toLowerCase().includes(q));
}
function rVisuals(){
  return `${coupler()}<h1>Visuals</h1>
  <p class="sub">Every visual of every page, in report order, with its fields and its own filters. Select one to open it on its page.</p>
  <input id="visual-search" class="search" placeholder="Search visuals, pages or fields…" aria-label="Search visuals" oninput="filterVisualList()">
  <p id="visual-count" class="mut" aria-live="polite" style="margin-bottom:.5rem"></p>
  <div class="column-scroll"><table class="t" id="visual-list"><thead><tr><th>Page</th><th>Visual</th><th>Fields</th><th>Filters on this visual</th></tr></thead><tbody id="visual-list-rows"></tbody></table></div>`;
}
function filterVisualList(){
  const body = document.getElementById('visual-list-rows'); if(!body) return;
  visibleVisuals = visualRows(document.getElementById('visual-search')?.value || '');
  const total = scopedPages().reduce((n, p) => n + p.visuals.length, 0);
  document.getElementById('visual-count').textContent = visibleVisuals.length === total ? plural(total, 'visual') : `${visibleVisuals.length} of ${plural(total, 'visual')}`;
  body.innerHTML = visibleVisuals.map(({p, v}) => `<tr><td><button class="xl" onclick="${action('showReportPage', p.id)}">${esc(pageTitle(p))}</button> ${pageBadges(p, {landing: false})}</td>
    <td><button class="xl" onclick="${action('showVisual', p.id, v.id)}">${esc(visualName(p.id, v))}</button>${v.hidden ? ' <span class="badge b-hidden">hidden</span>' : ''}<div class="mut">${esc(visualTypeName(v.type))}${isDecorative(v) ? ' · no data' : ''}</div></td>
    <td class="mut">${uniqueFields(v).map(fieldChip).join(' ') || '—'}</td>
    <td class="mut">${(v.filters || []).map(f => `${fieldRef(f)}${f.raw ? ` ${esc(f.raw)}` : ''}`).join('<br>') || '—'}</td></tr>`).join('')
    || `<tr class="none"><td colspan="4">${total ? 'No visuals match this search.' : 'No visuals in this selection.'}</td></tr>`;
}

/* ------------------------------------------------------------ Filters: the flat list, for searching */
// The analysis keeps one row per page for a filter on all pages; the list shows it once, as the report declares it.
function filterList(rows = allFilters()){
  const seen = new Set();
  return rows.filter(f => {
    if(f.level !== 'report') return true;
    const key = JSON.stringify([f.name || '', f.table, f.field, f.filterType, f.raw]);
    if(seen.has(key)) return false;
    seen.add(key);
    return true;
  }).map(f => f.level === 'report' ? {...f, pageLabel: 'Every page', target: ''} : f);
}
const FILTER_SCOPES = {report: 'All pages', page: 'Page', visual: 'Visual'};
// A field the page is reached through: a drillthrough field, or on a tooltip page a tooltip field.
const fieldWell = f => !f.drillthrough ? '' : (R.pages || []).find(p => p.id === f.pageId)?.pageType === 'tooltip' ? 'tooltip field' : 'drillthrough field';
function filterRowText(f){ return [FILTER_SCOPES[f.level] || f.level, f.pageLabel, f.target, f.displayName, `${f.table || ''}[${f.field || ''}]`, f.filterType, f.raw, fieldWell(f), f.isHidden ? 'hidden' : ''].join(' ').toLowerCase(); }
function rFilters(){
  const fs = filterList();
  if(!fs.length) return `${coupler()}<h1>Filters</h1><div class="empty"><b>No filters found</b> at report, page, or visual level.</div>`;
  return `${coupler()}<h1>Filters</h1>
  <p class="sub">Every filter the report declares, one row each, with the scope it applies at: all pages, one page or one visual. The same filters are grouped as the Filters pane groups them on each page in <button class="xl" onclick="switchTab('pages')">Pages</button>.</p>
  <input id="filter-search" class="search" placeholder="Search fields, pages or conditions…" aria-label="Search filters" oninput="filterFilterList()">
  <p id="filter-count" class="mut" aria-live="polite" style="margin-bottom:.5rem"></p>
  <div class="column-scroll"><table class="t" id="filter-list"><thead><tr><th>Scope</th><th>Applies to</th><th>Field</th><th>Type</th><th>Condition hint</th></tr></thead><tbody id="filter-list-rows"></tbody></table></div>`;
}
function filterFilterList(){
  const body = document.getElementById('filter-list-rows'); if(!body) return;
  const q = (document.getElementById('filter-search')?.value || '').trim().toLowerCase(), all = filterList();
  const rows = all.filter(f => !q || filterRowText(f).includes(q));
  document.getElementById('filter-count').textContent = rows.length === all.length ? plural(all.length, 'filter') : `${rows.length} of ${plural(all.length, 'filter')}`;
  body.innerHTML = rows.map(f => `<tr>
    <td><span class="badge b-other">${esc(FILTER_SCOPES[f.level] || f.level)}</span>${f.drillthrough ? `<div class="mut">${fieldWell(f)}</div>` : ''}</td>
    <td class="mut">${esc(f.pageLabel)}<div>${esc(f.target)}</div></td>
    <td>${f.displayName ? `<b>${esc(f.displayName)}</b><br>` : ''}${fieldRef(f)}${f.isHidden ? ' <span class="badge b-hidden">hidden from readers</span>' : ''}${f.isLocked ? ' <span class="badge b-other">locked</span>' : ''}</td>
    <td class="mut">${esc(f.filterType)}</td>
    <td class="mut">${f.raw ? esc(f.raw) : '—'}</td></tr>`).join('') || '<tr class="none"><td colspan="5">No filters match this search.</td></tr>';
}

/* ------------------------------------------------------------ Bookmarks */
function bookmarkPage(b){
  if(!b.page) return '<span class="mut">Not recorded</span>';
  const p = (R.pages || []).find(q => q.id === b.page);
  return p ? `<button class="xl" onclick="${action('showReportPage', p.id)}">${esc(pageTitle(p))}</button>` : `<span class="mut" title="${esc(b.page)}">A page that is not in this report</span>`;
}
function rBookmarks(){
  const all = R.bookmarks || [];
  const head = `${coupler()}<h1>Bookmarks</h1><p class="sub">Every bookmark read from the report, with its group, the page it opens and the fields its saved state refers to. A PBIR report and the legacy layout keep the bookmarks' order; a pbi-tools extract does not, so its bookmarks are listed by folder name.</p>`;
  if(!all.length) return `${head}<div class="empty"><b>No bookmarks were found in this report.</b></div>`;
  return `${head}<table class="t" id="bookmark-list"><thead><tr><th>Bookmark</th><th>Opens page</th><th>Fields its saved state refers to</th></tr></thead><tbody>${all.map(b => {
    const fields = (b.fields || []).filter((f, i, a) => a.findIndex(g => g.table === f.table && g.field === f.field) === i);
    return `<tr><th scope="row">${esc(b.name)}${b.group ? `<div class="mut">Group: ${esc(b.group)}</div>` : ''}</th><td>${bookmarkPage(b)}</td><td class="mut">${fields.map(f => `<span class="ref">${esc(f.table ? `${f.table}[${f.field}]` : `[${f.field}]`)}</span>${fx(f.table, f.field)}`).join(' ') || 'No field references'}</td></tr>`;
  }).join('')}</tbody></table>`;
}
Object.assign(RENDER, {pages: rPages, visuals: rVisuals, filters: rFilters, bookmarks: rBookmarks});
