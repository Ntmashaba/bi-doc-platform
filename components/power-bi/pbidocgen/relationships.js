/* Model view › Relationships: one interactive surface (UI rework, Change 7).

   Tables are boxes and relationships are lines, drawn as plain SVG with no library. Every line carries the
   cardinality at each end and the cross-filter direction at its middle. Selecting a table highlights its
   relationships, fades the rest and opens the detail in the panel beside the diagram, so the reader never
   scrolls between a diagram and a table. The full list is its own tab (Relationships list).

   What stays visibly distinct, without relying on colour alone:
     inactive relationship   dashed line
     single direction        one arrowhead, pointing the way filters flow (the "to" table filters the "from" table)
     both directions         two arrowheads, back to back
     automatic / unrecognised a hollow dot; the panel gives the value as the file writes it

   Automatic date tables and their relationships are not drawn (they are in no headline count); the
   Relationships list keeps them in a closed block. */
let relZoom = 0;                      // 0: as large as fits, but never too small to read (then it scrolls)
let relSel = {table: '', rel: -1};    // the selected table, or one relationship (its index in countedRelationships())
const REL_BOX_W = 176, REL_BOX_H = 52, REL_LANE_GAP = 104, REL_ROW_GAP = 16, REL_PAD = 24, REL_PORT = 15, REL_LOOP = 40;
const REL_READABLE = .75;             // below this the names are too small, so the diagram scrolls instead

const relCardinality = v => ({many: '*', one: '1'})[String(v || '').toLowerCase()] || '?';
const relCardinalityWord = v => ['many', 'one'].includes(String(v || '').toLowerCase()) ? String(v).toLowerCase() : 'not recorded';
// The cross-filter direction of a relationship, as a kind for drawing and in words.
function relFilter(r){
  const v = r.crossFilteringBehavior;
  if(v === 'bothDirections') return {kind: 'both', label: 'both', text: 'Filters both ways'};
  if(!v || v === 'oneDirection' || v === 'singleDirection') return {kind: 'single', label: 'single', text: `Single direction: ${r.toTable} filters ${r.fromTable}`};
  if(v === 'automatic') return {kind: 'automatic', label: 'automatic', text: 'Automatic: Power BI decides the direction'};
  return {kind: 'other', label: String(v), text: `Cross-filter value "${v}", shown as the file writes it`};
}
const relSentence = r => `${r.fromTable}[${r.fromColumn || '?'}]${fxText(r.fromTable, r.fromColumn)} → ${r.toTable}[${r.toColumn || '?'}]${fxText(r.toTable, r.toColumn)}`;
const relFacts = r => `${relCardinalityWord(r.fromCardinality)} to ${relCardinalityWord(r.toCardinality)} · ${r.isActive === false ? 'inactive' : 'active'} · ${relFilter(r).text}`;

/* Where everything goes. Fact tables stand in the middle lane; a group of tables with no fact gets its
   best-connected table there instead. Whatever hangs off the middle is placed whole on one side, one lane
   per step away, so no line has to cross the middle lane. Tables without relationships sit in a grid below. */
function relLayout(room = 0){
  const tables = countedTables(), known = new Set(tables.map(t => t.name));
  const edges = countedRelationships().map((r, i) => ({r, i})).filter(e => known.has(e.r.fromTable) && known.has(e.r.toTable));
  const adj = new Map();
  for(const {r} of edges) for(const [a, b] of [[r.fromTable, r.toTable], [r.toTable, r.fromTable]]){
    if(!adj.has(a)) adj.set(a, new Set());
    if(a !== b) adj.get(a).add(b);
  }
  const inGraph = tables.filter(t => adj.has(t.name)).map(t => t.name);
  const orphans = tables.filter(t => !adj.has(t.name)).map(t => t.name);
  const role = new Map(tables.map(t => [t.name, t.tableType]));
  const centres = new Set(inGraph.filter(n => role.get(n) === 'fact'));
  const walk = (start, seen, through = () => true) => {
    const group = [start]; seen.add(start);
    for(let k = 0; k < group.length; k++) for(const n of adj.get(group[k])) if(!seen.has(n) && through(n)){ seen.add(n); group.push(n); }
    return group;
  };
  const visited = new Set();
  for(const start of inGraph){
    if(visited.has(start)) continue;
    const group = walk(start, visited);
    if(!group.some(n => centres.has(n))) centres.add(group.reduce((best, n) => adj.get(n).size > adj.get(best).size ? n : best, group[0]));
  }
  const dist = new Map(), queue = inGraph.filter(n => centres.has(n));
  for(const n of queue) dist.set(n, 0);
  for(let k = 0; k < queue.length; k++) for(const n of adj.get(queue[k])) if(!dist.has(n)){ dist.set(n, dist.get(queue[k]) + 1); queue.push(n); }
  const side = new Map(), load = [0, 0], placed = new Set(centres), groups = [];
  for(const start of inGraph) if(!placed.has(start)) groups.push(walk(start, placed, n => !centres.has(n)));
  groups.sort((a, b) => b.length - a.length);
  for(const group of groups){
    const s = load[0] <= load[1] ? 0 : 1;
    load[s] += group.filter(n => dist.get(n) === 1).length || 1;
    for(const n of group) side.set(n, s ? 1 : -1);
  }
  const laneOf = n => centres.has(n) ? 0 : side.get(n) * dist.get(n);
  const lanes = new Map();
  for(const n of inGraph){ const l = laneOf(n); if(!lanes.has(l)) lanes.set(l, []); lanes.get(l).push(n); }
  const laneIds = [...lanes.keys()].sort((a, b) => a - b);
  // Within a lane, a table sits near the tables it is related to in the lane next to it, towards the middle.
  const rank = new Map();
  const settle = l => lanes.get(l).forEach((n, i) => rank.set(n, lanes.get(l).length > 1 ? i / (lanes.get(l).length - 1) : .5));
  if(lanes.has(0)) settle(0);
  for(const l of laneIds.filter(l => l !== 0).sort((a, b) => Math.abs(a) - Math.abs(b))){
    const inner = l > 0 ? l - 1 : l + 1;
    const pull = n => { const near = [...adj.get(n)].filter(m => laneOf(m) === inner && rank.has(m)); return near.length ? near.reduce((s, m) => s + rank.get(m), 0) / near.length : .5; };
    const order = new Map(lanes.get(l).map((n, i) => [n, [pull(n), i]]));
    lanes.get(l).sort((a, b) => order.get(a)[0] - order.get(b)[0] || order.get(a)[1] - order.get(b)[1]);
    settle(l);
  }
  // Each line leaves a box on the side facing the other table; two tables in one lane are joined round the outside.
  const ports = new Map(inGraph.map(n => [n, {'-1': [], '1': []}]));
  for(const e of edges){
    const a = e.r.fromTable, b = e.r.toTable, la = laneOf(a), lb = laneOf(b);
    if(la === lb){ e.loop = true; e.sa = e.sb = la < 0 ? -1 : 1; }
    else { e.sa = la < lb ? 1 : -1; e.sb = -e.sa; }
    ports.get(a)[e.sa].push({e, end: 'a', other: b});
    ports.get(b)[e.sb].push({e, end: 'b', other: a});
  }
  const height = n => Math.max(REL_BOX_H, 22 + REL_PORT * Math.max(ports.get(n)[-1].length, ports.get(n)[1].length));
  const laneHeight = l => lanes.get(l).reduce((s, n) => s + height(n), 0) + REL_ROW_GAP * (lanes.get(l).length - 1);
  const graphH = Math.max(0, ...laneIds.map(laneHeight));
  const loopsLeft = edges.some(e => e.loop && e.sa < 0 && laneOf(e.r.fromTable) === laneIds[0]) ? REL_LOOP + 30 : 0;
  const loopsRight = edges.some(e => e.loop && e.sa > 0 && laneOf(e.r.fromTable) === laneIds[laneIds.length - 1]) ? REL_LOOP + 30 : 0;
  const graphW = laneIds.length ? laneIds.length * REL_BOX_W + (laneIds.length - 1) * REL_LANE_GAP + loopsLeft + loopsRight : 0;
  const gridCols = orphans.length ? Math.max(1, Math.floor((Math.max(graphW, Math.min(room, 1500) - 2 * REL_PAD, 3 * REL_BOX_W + 2 * REL_ROW_GAP) + REL_ROW_GAP) / (REL_BOX_W + REL_ROW_GAP))) : 0;
  const gridW = orphans.length ? Math.min(gridCols, orphans.length) * (REL_BOX_W + REL_ROW_GAP) - REL_ROW_GAP : 0;
  const W = Math.max(graphW, gridW) + 2 * REL_PAD;
  const left = (W - graphW) / 2 + loopsLeft;
  const pos = new Map();
  laneIds.forEach((l, column) => {
    let y = REL_PAD + (graphH - laneHeight(l)) / 2;
    for(const n of lanes.get(l)){ pos.set(n, {x: left + column * (REL_BOX_W + REL_LANE_GAP), y, w: REL_BOX_W, h: height(n)}); y += height(n) + REL_ROW_GAP; }
  });
  const middle = n => pos.get(n).y + pos.get(n).h / 2;
  for(const n of inGraph) for(const s of [-1, 1]){
    const list = ports.get(n)[s], p = pos.get(n);
    list.sort((u, v) => middle(u.other) - middle(v.other) || u.e.i - v.e.i);
    list.forEach((port, k) => {
      const x = s > 0 ? p.x + p.w : p.x, y = p.y + (k + 1) * p.h / (list.length + 1);
      if(port.end === 'a'){ port.e.x1 = x; port.e.y1 = y; } else { port.e.x2 = x; port.e.y2 = y; }
    });
  }
  let loops = 0;
  for(const e of edges) if(e.loop) e.bend = REL_LOOP + 9 * (loops++ % 3);
  const gridTop = REL_PAD + graphH + (inGraph.length ? 46 : 18);
  orphans.forEach((n, k) => pos.set(n, {x: (W - gridW) / 2 + (k % gridCols) * (REL_BOX_W + REL_ROW_GAP),
    y: gridTop + Math.floor(k / gridCols) * (REL_BOX_H + REL_ROW_GAP), w: REL_BOX_W, h: REL_BOX_H, alone: true}));
  const gridRows = orphans.length ? Math.ceil(orphans.length / gridCols) : 0;
  const H = (orphans.length ? gridTop + gridRows * (REL_BOX_H + REL_ROW_GAP) - REL_ROW_GAP : REL_PAD + graphH) + REL_PAD;
  return {W, H, pos, edges, inGraph, orphans, gridTop, tables};
}
// The line of a relationship, the point at its middle and the way it runs there.
function relPath(e){
  const {x1, y1, x2, y2} = e;
  if(e.loop){
    const out = e.sa * e.bend;
    return {d: `M${x1},${y1} C${x1 + out},${y1} ${x2 + out},${y2} ${x2},${y2}`, mx: (x1 + x2) / 2 + out * .75, my: (y1 + y2) / 2, angle: y2 >= y1 ? 90 : -90};
  }
  const mid = (x1 + x2) / 2;
  return {d: `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`, mx: mid, my: (y1 + y2) / 2,
          angle: Math.atan2(1.5 * (y2 - y1), .75 * (x2 - x1)) * 180 / Math.PI};
}
const relRound = n => Math.round(n * 10) / 10;
function relEdgeSvg(e, state){
  const r = e.r, f = relFilter(r), p = relPath(e), inactive = r.isActive === false;
  const at = `translate(${relRound(p.mx)},${relRound(p.my)}) rotate(${relRound(p.angle + 180)})`;   // towards the "from" table
  const mark = f.kind === 'single' ? `<path class="rel-arrow" d="M-7,-5.5 L5,0 L-7,5.5 Z" transform="${at}"/>`
    : f.kind === 'both' ? `<path class="rel-arrow" d="M2,-5.5 L12,0 L2,5.5 Z M-2,-5.5 L-12,0 L-2,5.5 Z" transform="${at}"/>`
    : `<circle class="rel-arrow rel-open" cx="${relRound(p.mx)}" cy="${relRound(p.my)}" r="4.5"/>`;
  const end = (x, y, s, v) => `<text class="rel-card" x="${relRound(x + s * 11)}" y="${relRound(y - 5)}" text-anchor="middle">${relCardinality(v)}</text>`;
  return `<g class="rel-edge ${inactive ? 'inactive' : 'active'} filter-${f.kind}${state ? ' is-' + state : ''}" id="rel-edge-${e.i}" data-rel="${e.i}"${state === 'faded' ? ' opacity="0.22"' : ''} onclick="selectRelationship(${e.i})">
    <title>${esc(relSentence(r))} · ${esc(relFacts(r))}</title>
    <path class="rel-hit" d="${p.d}" fill="none" stroke="transparent" stroke-width="14"/>
    <path class="rel-line" d="${p.d}" fill="none" stroke-width="${state === 'hot' ? 3 : 1.75}"${inactive ? ' stroke-dasharray="7 5"' : ''}/>
    ${mark}${end(e.x1, e.y1, e.sa, r.fromCardinality)}${end(e.x2, e.y2, e.sb, r.toCardinality)}</g>`;
}
const REL_ROLES = {fact: 'fact', dimension: 'dim', 'date dimension': 'date'};
function relBoxSvg(name, p, state, count){
  const t = M.tables.find(t => t.name === name) || {}, index = M.tables.indexOf(t);
  const label = name.length > 21 ? name.slice(0, 20) + '…' : name, pick = action('selectRelTable', name);
  const said = `${name}, ${t.tableType || 'table'}, ${count ? plural(count, 'relationship') : 'no relationships'}`;
  return `<g class="rel-box role-${REL_ROLES[t.tableType] || 'other'}${p.alone ? ' no-rel' : ''}${state ? ' is-' + state : ''}" id="rel-box-${index}" data-table="${esc(name)}" tabindex="0" role="button" aria-pressed="${state === 'selected'}" aria-label="${esc(said)}"${state === 'faded' ? ' opacity="0.3"' : ''} onclick="${pick}" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();${pick}}">
    <title>${esc(said)}</title>
    <rect x="${relRound(p.x)}" y="${relRound(p.y)}" width="${p.w}" height="${relRound(p.h)}" rx="8" stroke-width="${state === 'selected' ? 3 : 1.5}"/>
    <text class="rel-name" x="${relRound(p.x + 12)}" y="${relRound(p.y + p.h / 2 - 3)}">${esc(label)}</text>
    <text class="rel-role" x="${relRound(p.x + 12)}" y="${relRound(p.y + p.h / 2 + 15)}">${esc(t.tableType || 'table')}</text></g>`;
}
function drawRelSurface(){
  const host = document.getElementById('rel-canvas'); if(!host) return;
  const viewport = document.getElementById('rel-viewport'), room = viewport?.clientWidth || 0;
  const L = relLayout(room);
  if(relSel.table && !L.pos.has(relSel.table)) relSel.table = '';
  if(relSel.rel >= 0 && !L.edges.some(e => e.i === relSel.rel)) relSel.rel = -1;
  const picked = !!relSel.table || relSel.rel >= 0;
  const hot = e => relSel.rel >= 0 ? e.i === relSel.rel : e.r.fromTable === relSel.table || e.r.toTable === relSel.table;
  const related = new Set(L.edges.filter(e => picked && hot(e)).flatMap(e => [e.r.fromTable, e.r.toTable]));
  const degree = new Map();
  for(const e of L.edges) for(const n of new Set([e.r.fromTable, e.r.toTable])) degree.set(n, (degree.get(n) || 0) + 1);
  const fit = room ? Math.min(1, (room - 2) / L.W) : 1, zoom = relZoom || Math.max(REL_READABLE, fit);
  if(!L.edges.length){
    const auto = (M.relationships || []).filter(autoDateRelationship).length;
    host.innerHTML = auto
      ? `<div class="empty">No relationships to draw: the model's only ${auto === 1 ? 'relationship is' : 'relationships are'} to automatic date tables, listed in the Relationships list.</div>`
      : '<div class="empty">No relationships in this model.</div>';
  } else {
    // Lines first, boxes after: a line never paints over a table's name.
    host.innerHTML = `<svg width="${relRound(L.W * zoom)}" height="${relRound(L.H * zoom)}" viewBox="0 0 ${relRound(L.W)} ${relRound(L.H)}" role="group" aria-label="Tables and their relationships">
      ${L.edges.map(e => relEdgeSvg(e, picked ? (hot(e) ? 'hot' : 'faded') : '')).join('')}
      ${L.orphans.length ? `<text class="rel-caption" x="${relRound(L.pos.get(L.orphans[0]).x)}" y="${relRound(L.gridTop - 12)}">No relationships</text>` : ''}
      ${[...L.pos].map(([name, p]) => relBoxSvg(name, p, !picked ? '' : name === relSel.table ? 'selected' : related.has(name) ? 'related' : 'faded', degree.get(name) || 0)).join('')}</svg>`;
  }
  const label = document.getElementById('rel-zoom-label'); if(label) label.textContent = Math.round(zoom * 100) + '%';
  const focus = document.getElementById('rel-focus'); if(focus) focus.value = relSel.table;
  renderRelPanel(L);
}
/* The panel beside the diagram: the model at a glance, one table's relationships, or one relationship. */
function relItem(r, from){
  const f = relFilter(r), other = from && r.fromTable === from ? r.toTable : from ? r.fromTable : '';
  const badges = `${r.isActive === false ? '<span class="badge b-hidden">inactive</span>' : ''}${f.kind === 'both' ? '<span class="badge b-warn">both ways</span>' : ''}`;
  const head = from
    ? `<button class="xl" onclick="${action('selectRelTable', other)}">${esc(other)}</button>${other === from ? ' <span class="mut">(itself)</span>' : ''}`
    : `<button class="xl" onclick="${action('selectRelTable', r.fromTable)}">${esc(r.fromTable)}</button> <span aria-hidden="true">→</span> <button class="xl" onclick="${action('selectRelTable', r.toTable)}">${esc(r.toTable)}</button>`;
  return `<li class="rel-item${r.isActive === false ? ' inactive' : ''}"><div class="rel-item-head">${head} ${badges}</div>
    <div class="rel-item-cols"><span class="rel-end"><span class="ref">${esc(r.fromTable)}[${esc(r.fromColumn || '?')}]</span>${fx(r.fromTable, r.fromColumn)}</span> <span class="rel-end"><span aria-hidden="true">→</span> <span class="ref">${esc(r.toTable)}[${esc(r.toColumn || '?')}]</span>${fx(r.toTable, r.toColumn)}</span></div>
    <div class="mut">${esc(relFacts(r))}</div></li>`;
}
function renderRelPanel(L){
  const panel = document.getElementById('rel-panel'); if(!panel) return;
  const all = L.edges.map(e => e.r), auto = (M.relationships || []).filter(autoDateRelationship);
  const tally = list => [plural(list.length, 'relationship'),
    list.some(r => r.isActive === false) ? `${list.filter(r => r.isActive === false).length} inactive` : '',
    list.some(r => relFilter(r).kind === 'both') ? `${list.filter(r => relFilter(r).kind === 'both').length} filtering both ways` : ''].filter(Boolean).join(' · ');
  const clear = '<button type="button" class="chip" id="rel-clear" onclick="clearRelSelection()">Clear selection</button>';
  const list = `<button class="xl" onclick="switchTab('rel-list')">Relationships list</button>`;
  if(relSel.rel >= 0){
    const r = countedRelationships()[relSel.rel];
    panel.innerHTML = `<div class="rel-panel-head"><h2>Relationship</h2>${clear}</div>
      <ul class="rel-items">${relItem(r, '')}</ul>
      <table class="t rel-facts"><tbody>
        <tr><th scope="row">From</th><td>${tblLink(r.fromTable)} <span class="mut">${esc(relCardinalityWord(r.fromCardinality))} side</span></td></tr>
        <tr><th scope="row">To</th><td>${tblLink(r.toTable)} <span class="mut">${esc(relCardinalityWord(r.toCardinality))} side</span></td></tr>
        <tr><th scope="row">Status</th><td>${r.isActive === false ? 'Inactive: used only when a measure asks for it (USERELATIONSHIP)' : 'Active'}</td></tr>
        <tr><th scope="row">Cross-filter</th><td>${esc(relFilter(r).text)}</td></tr></tbody></table>
      <p class="mut">Select either table to see all of its relationships.</p>`;
    return;
  }
  if(relSel.table){
    const t = M.tables.find(t => t.name === relSel.table), mine = all.filter(r => r.fromTable === t.name || r.toTable === t.name);
    const autoMine = auto.filter(r => r.fromTable === t.name || r.toTable === t.name);
    panel.innerHTML = `<div class="rel-panel-head"><h2>${esc(t.name)}</h2>${clear}</div>
      <p>${typeBadge(t.tableType || 'table')} <span class="defined">defined by <b>${esc(definedLabel(t))}</b></span></p>
      <p>${tblLink(t.name)} <span class="mut">opens its columns in Table view</span></p>
      <h3>${mine.length ? esc(tally(mine)) : 'No relationships'}</h3>
      ${mine.length ? `<ul class="rel-items">${mine.map(r => relItem(r, t.name)).join('')}</ul>` : '<p class="mut">This table is related to no other table in the model.</p>'}
      ${autoMine.length ? `<p class="mut">Also ${plural(autoMine.length, 'relationship')} to ${autoMine.length === 1 ? 'an automatic date table' : 'automatic date tables'}, not drawn here; see the ${list}.</p>` : ''}`;
    return;
  }
  panel.innerHTML = `<div class="rel-panel-head"><h2>This model</h2></div>
    <p>${plural(L.tables.length, 'table')} · ${esc(tally(all))}</p>
    <p class="mut">Select a table to see its relationships. Its lines stay, the rest fades, and the detail opens here.</p>
    ${L.orphans.length && all.length ? `<p class="mut">${plural(L.orphans.length, 'table')} with no relationships ${L.orphans.length === 1 ? 'is' : 'are'} at the foot of the diagram.</p>` : ''}
    ${auto.length ? `<p class="mut">${plural(auto.length, 'relationship')} to ${auto.length === 1 ? 'an automatic date table is' : 'automatic date tables are'} not drawn; ${auto.length === 1 ? 'it is' : 'they are'} in the ${list}.</p>` : ''}
    <p>Every relationship, one row each: ${list}.</p>`;
}
// Redrawing replaces the SVG, so keyboard focus is put back on the box it was on.
function relRedraw(focusTable){
  const within = typeof document.activeElement === 'object' && document.activeElement?.closest?.('#rel-canvas');
  drawRelSurface();
  if(within && focusTable !== undefined){
    const index = M.tables.findIndex(t => t.name === focusTable);
    document.getElementById('rel-box-' + index)?.focus?.({preventScroll: true});
  }
}
// On opening: the selection if there is one, otherwise the middle lane (the fact tables), in view.
function relReveal(){
  const viewport = document.getElementById('rel-viewport'); if(!viewport || !viewport.scrollWidth) return;
  const target = relSel.rel >= 0 ? document.getElementById('rel-edge-' + relSel.rel)
    : relSel.table ? document.getElementById('rel-box-' + M.tables.findIndex(t => t.name === relSel.table)) : null;
  if(target?.scrollIntoView){ target.scrollIntoView({block: 'nearest', inline: 'center', behavior: 'instant'}); return; }
  const middle = [...document.querySelectorAll('#rel-canvas .rel-box.role-fact')][0] || document.querySelector('#rel-canvas .rel-box:not(.no-rel)');
  if(!middle?.getBoundingClientRect) return;
  const box = middle.getBoundingClientRect(), frame = viewport.getBoundingClientRect();
  viewport.scrollLeft += box.left + box.width / 2 - (frame.left + frame.width / 2);
}
function selectRelTable(name, reveal = false){
  const again = relSel.rel < 0 && relSel.table === name;
  relSel = {table: again || !name ? '' : name, rel: -1};
  relRedraw(name);
  if(reveal && relSel.table){
    const index = M.tables.findIndex(t => t.name === name);
    document.getElementById('rel-box-' + index)?.scrollIntoView?.({block: 'nearest', inline: 'nearest', behavior: 'instant'});
  }
}
function selectRelationship(index){
  relSel = {table: '', rel: relSel.rel === index ? -1 : index};
  relRedraw();
}
function clearRelSelection(){ relSel = {table: '', rel: -1}; relRedraw(); }
// Opening the surface afresh (a count, a link, a test) forgets the last selection and zoom.
function resetRelView(){ relSel = {table: '', rel: -1}; relZoom = 0; }
function zoomRel(delta){
  const shown = parseInt(document.getElementById('rel-zoom-label')?.textContent, 10) / 100 || 1;
  relZoom = Math.max(.3, Math.min(2.5, Math.round((shown + delta) * 20) / 20));
  relRedraw();
}
function fitRel(){
  const viewport = document.getElementById('rel-viewport'), room = viewport?.clientWidth || 0;
  relZoom = room ? Math.max(.3, Math.min(1, (room - 2) / relLayout(room).W)) : 0;
  relRedraw(); if(viewport){ viewport.scrollLeft = 0; viewport.scrollTop = 0; }
}
// From the Relationships list: the same relationship, on the diagram.
function showRelationship(index){ relSel = {table: '', rel: index}; switchTab('rels'); }
function relSurfaceKey(e){ if(e.key === 'Escape' && (relSel.table || relSel.rel >= 0)){ e.preventDefault(); clearRelSelection(); document.getElementById('rel-viewport')?.focus?.(); } }
function relCanvasClick(e){ if(!e.target.closest || !e.target.closest('.rel-box,.rel-edge')) { if(relSel.table || relSel.rel >= 0) clearRelSelection(); } }

function rRels(){
  const tables = countedTables();
  return `${coupler()}<h1>Relationships</h1>
  <p class="sub" style="margin-bottom:16px">Tables are boxes, relationships are the lines between them. A line shows the cardinality at each end and, at its middle, which way filters flow. Select a table to keep its relationships and fade the rest; its detail opens in the panel.</p>
  <div class="card rel-surface" id="rel-surface" onkeydown="relSurfaceKey(event)">
    <div class="rel-main">
      <div class="erd-toolbar"><label>Table <select id="rel-focus" onchange="selectRelTable(this.value,true)"><option value="">Choose a table…</option>${tables.map(t => `<option value="${esc(t.name)}"${relSel.table === t.name ? ' selected' : ''}>${esc(t.name)}</option>`).join('')}</select></label>
        <div class="erd-zoom" role="group" aria-label="Diagram zoom"><button type="button" aria-label="Zoom out" onclick="zoomRel(-0.2)">−</button><output id="rel-zoom-label" aria-live="polite">100%</output><button type="button" aria-label="Zoom in" onclick="zoomRel(0.2)">+</button><button type="button" onclick="fitRel()">Fit</button></div></div>
      <div id="rel-viewport" class="erd-viewport rel-viewport" tabindex="0" role="region" aria-label="Relationship diagram; scroll to explore" onclick="relCanvasClick(event)"><div id="rel-canvas"></div></div>
      <div class="legend rel-legend" id="rel-legend">
        <span><svg width="34" height="12" aria-hidden="true"><path class="rel-line" d="M1,6 H33" stroke-width="1.75"/></svg>Active</span>
        <span><svg width="34" height="12" aria-hidden="true"><path class="rel-line" d="M1,6 H33" stroke-width="1.75" stroke-dasharray="7 5"/></svg>Inactive</span>
        <span><svg width="34" height="12" aria-hidden="true"><path class="rel-line" d="M1,6 H33" stroke-width="1.75"/><path class="rel-arrow" d="M-7,-5.5 L5,0 L-7,5.5 Z" transform="translate(17,6)"/></svg>Filters one way</span>
        <span class="filter-both"><svg width="34" height="12" aria-hidden="true"><path class="rel-line" d="M1,6 H33" stroke-width="1.75"/><path class="rel-arrow" d="M2,-5.5 L12,0 L2,5.5 Z M-2,-5.5 L-12,0 L-2,5.5 Z" transform="translate(17,6)"/></svg>Filters both ways</span>
        ${countedRelationships().some(r => ['automatic', 'other'].includes(relFilter(r).kind)) ? `<span><svg width="34" height="12" aria-hidden="true"><path class="rel-line" d="M1,6 H33" stroke-width="1.75"/><circle class="rel-arrow rel-open" cx="17" cy="6" r="4.5"/></svg>Direction automatic or not recognised</span>` : ''}
        <span><b>1</b> one · <b>*</b> many</span></div>
    </div>
    <aside id="rel-panel" class="rel-panel" aria-live="polite" aria-label="Details of the selection"></aside>
  </div>`;
}

/* Model view › Relationships list: every relationship, one row each. */
function relationshipRow(r, index){
  const f = relFilter(r);
  return `<tr>
    <td>${tblLink(r.fromTable)} <span class="ref">[${esc(r.fromColumn)}]</span>${fx(r.fromTable, r.fromColumn)}</td>
    <td class="mut">${esc(r.fromCardinality)} → ${esc(r.toCardinality)}</td>
    <td>${tblLink(r.toTable)} <span class="ref">[${esc(r.toColumn)}]</span>${fx(r.toTable, r.toColumn)}</td>
    <td>${r.isActive ? '<span class="badge b-direct">active</span>' : '<span class="badge b-hidden">inactive</span>'}</td>
    <td>${f.kind === 'both' ? '<span class="badge b-warn">both</span>' : esc(f.label)}</td>
    ${index === undefined ? '' : `<td><button class="xl" onclick="showRelationship(${index})" aria-label="Show ${esc(r.fromTable)} to ${esc(r.toTable)} on the diagram">Show</button></td>`}</tr>`;
}
const REL_LIST_HEAD = '<th>From table / column</th><th>Cardinality</th><th>To table / column</th><th>Status</th><th>Cross-filter</th>';
function relationshipRows(query = ''){
  const q = query.trim().toLowerCase();
  return countedRelationships().map((r, i) => [r, i])
    .filter(([r]) => !q || [r.fromTable, r.fromColumn, r.toTable, r.toColumn, r.isActive ? 'active' : 'inactive', relFilter(r).label].join(' ').toLowerCase().includes(q))
    .map(([r, i]) => relationshipRow(r, i)).join('');
}
function rRelList(){
  return `${coupler()}<h1>Relationships list</h1>
  <p class="sub">Every relationship in the model, one row each. <b>Show</b> opens the same relationship on the <button class="xl" onclick="switchTab('rels')">diagram</button>.</p>
  <input id="rel-list-search" class="search" placeholder="Search relationships…" aria-label="Search relationships" oninput="filterRelList()">
  <p id="rel-list-count" class="mut" aria-live="polite" style="margin-bottom:.5rem"></p>
  <div class="column-scroll"><table class="t" id="rel-list"><thead><tr>${REL_LIST_HEAD}<th>Diagram</th></tr></thead><tbody id="rel-list-rows"></tbody></table></div>
  ${rAutoDateRelationships()}`;
}
function filterRelList(){
  const body = document.getElementById('rel-list-rows'); if(!body) return;
  const rows = relationshipRows(document.getElementById('rel-list-search')?.value || ''), total = countedRelationships().length;
  const shown = (rows.match(/<tr>/g) || []).length;
  body.innerHTML = rows || `<tr class="rel-none"><td colspan="6">${total ? 'No relationships match this search.' : 'No relationships in this model.'}</td></tr>`;
  document.getElementById('rel-list-count').textContent = shown === total ? plural(total, 'relationship') : `${shown} of ${plural(total, 'relationship')}`;
}
function rAutoDateRelationships(){
  const auto = (M.relationships || []).filter(autoDateRelationship);
  if(!auto.length) return '';
  return `<details class="rel-auto" id="rel-auto"><summary>Relationships to automatic date tables <span class="n">${auto.length}</span>
    <span class="mut" style="font-weight:400">added by Power BI for date columns; in no headline count</span></summary>
    <div class="body"><table class="t"><thead><tr>${REL_LIST_HEAD}</tr></thead><tbody>${auto.map(r => relationshipRow(r)).join('')}</tbody></table></div></details>`;
}
RENDER['rels'] = rRels;
RENDER['rel-list'] = rRelList;
