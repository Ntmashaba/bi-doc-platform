/* Power Query view: the queries of the file, laid out like the Power Query Editor.

   Left, the queries pane, with the folders the file records. Right, the selected query: what it is and what
   it feeds, three statuses that are reported separately (was the whole expression read, could its Applied
   Steps be read, is the script in this copy of the document), the steps, and the script as written.
   The facts come from DERIVED.powerQuery, worked out when the page was rendered; the script itself is read
   from DATA.sourceQueries, so it is on the page once. */
const PQ = (typeof DERIVED !== 'undefined' && DERIVED && DERIVED.powerQuery) || {queries: [], groups: [], recorded: false};
const PQ_BY_ID = new Map(PQ.queries.map(q => [q.objectId, q]));
const PQ_OTHER = 'Other Queries';
let pqSelected = null;
const pqItemId = id => 'pq-item-' + slug(id);
const pqCode = q => String((DATA.sourceQueries || [])[q.row]?.mCode || '');
const pqByOrder = (a, b) => (a.order ?? 0) - (b.order ?? 0) || a.name.localeCompare(b.name);
const pqKindName = {query: 'Query', function: 'Function', parameter: 'Parameter'};
const queryLink = id => {
  const q = PQ_BY_ID.get(id);
  return q ? `<button class="xl" onclick="${action('goObject', id)}">${esc(q.name)}</button>` : '';
};

/* Folders in the order the file gives them; a folder whose path continues another's sits under it.
   Queries outside every folder follow under "Other Queries", as in the editor. With no folders the list is flat. */
function pqFolders() {
  const named = PQ.groups.map(g => g.folder);
  for (const q of PQ.queries) if (q.group && !named.includes(q.group)) named.push(q.group);
  const depthOf = folder => named.filter(other => other !== folder && other.length < folder.length &&
    folder.startsWith(other) && '\\/'.includes(folder[other.length])).length;
  const folders = named.map(folder => {
    const parent = named.filter(other => other !== folder && folder.startsWith(other) && '\\/'.includes(folder[other.length]))
      .sort((a, b) => b.length - a.length)[0];
    return {folder, label: parent ? folder.slice(parent.length + 1) : folder, depth: depthOf(folder),
      description: PQ.groups.find(g => g.folder === folder)?.description || '',
      queries: PQ.queries.filter(q => q.group === folder).sort(pqByOrder)};
  });
  const loose = PQ.queries.filter(q => !q.group).sort(pqByOrder);
  if (!folders.length) return [{folder: '', label: '', depth: 0, description: '', queries: loose}];
  if (loose.length) folders.push({folder: '', label: PQ_OTHER, depth: 0, description: '', queries: loose});
  return folders;
}
function pqListItem(q) {
  const loaded = q.load === 'loaded';
  const mark = q.kind === 'function' ? '<span class="pq-kind" title="Function">fx</span>'
    : q.kind === 'parameter' ? '<span class="pq-kind" title="Parameter">param</span>' : '';
  return `<li><button type="button" class="pq-item${loaded ? '' : ' pq-unloaded'}" id="${pqItemId(q.objectId)}" data-name="${esc(q.name.toLowerCase())}"
    aria-current="${q.objectId === pqSelected}" title="${esc(q.name)}${q.load ? ' · ' + esc(q.load) : ''}" onclick="${action('selectQuery', q.objectId)}">
    <span class="pq-name">${esc(q.name)}</span>${mark}</button></li>`;
}
function rPowerQuery() {
  const intro = `<h1>Power Query</h1>
    <p class="sub">The Power Query Editor in Power BI Desktop: the queries that connect to sources and shape the data before it reaches the model.</p>`;
  if (!PQ.queries.length) return `${intro}<div class="empty"><b>This file holds no Power Query queries.</b><br>Its tables are defined another way; see how each table is defined in the table list.</div>`;
  if (!PQ_BY_ID.has(pqSelected)) pqSelected = (pqFolders().find(f => f.queries.length) || {queries: [PQ.queries[0]]}).queries[0].objectId;
  const folders = pqFolders();
  const list = folders.map(f => `${f.label ? `<h3 class="pq-folder" style="padding-left:${0.6 + f.depth * 0.9}rem" title="${esc(f.description || f.folder)}">${esc(f.label)} <span class="n">${f.queries.length}</span></h3>` : ''}
    <ul class="pq-list" style="padding-left:${f.depth * 0.9}rem">${f.queries.map(pqListItem).join('')}</ul>`).join('');
  const counts = ['query', 'function', 'parameter'].map(k => [k, PQ.queries.filter(q => (q.kind || 'query') === k).length]).filter(([, n]) => n);
  return `${intro}
    <div class="pq">
      <section class="pq-pane" aria-label="Queries">
        <div class="pq-pane-head"><b>Queries</b> <span class="n">${PQ.queries.length}</span></div>
        <input id="pq-filter" class="search" placeholder="Filter queries…" aria-label="Filter queries by name" oninput="pqFilter()">
        <p id="pq-filter-status" class="mut" role="status" aria-live="polite"></p>
        <div class="pq-tree" role="group" aria-label="Queries${PQ.groups.length ? ' by folder' : ''}">${list}</div>
        <p class="mut pq-pane-foot">${counts.map(([k, n]) => plural(n, pqKindName[k].toLowerCase(), k === 'query' ? 'queries' : undefined)).join(' · ')}${PQ.groups.length ? '' : ' · the file records no query folders'}</p>
        <button class="chip" onclick="downloadQueryCsv()">Export all M queries CSV</button>
      </section>
      <section class="pq-detail" id="pq-detail" aria-live="polite">${pqDetail(PQ_BY_ID.get(pqSelected))}</section>
    </div>`;
}
const PQ_STATUS = {
  extraction: {complete: ['b-direct', 'Complete'], 'known partial': ['b-warn', 'Known partial'], unavailable: ['b-none', 'Unavailable'],
    'not recorded': ['b-info', 'Not recorded']},
  steps: {parsed: ['b-direct', 'Parsed'], none: ['b-info', 'No top-level Applied Steps'], unsupported: ['b-warn', 'Unsupported syntax'],
    'not recorded': ['b-info', 'Not recorded']},
  publication: {included: ['b-direct', 'Included'], cleaned: ['b-warn', 'Cleaned'], withheld: ['b-hidden', 'Withheld']},
};
function pqStatus(kind, title, status, note) {
  const [cls, label] = PQ_STATUS[kind][status] || ['b-info', status || 'Not recorded'];
  return `<div class="pq-status"><dt>${title}</dt><dd><span class="badge ${cls}">${esc(label)}</span>${note ? `<span class="pq-status-note">${esc(note)}</span>` : ''}</dd></div>`;
}
function pqStatuses(q) {
  const steps = q.steps || {}, count = (steps.items || []).length;
  const stepNote = steps.status === 'parsed'
    ? (q.publication === 'withheld' ? 'Read when the document was generated; the names are part of the withheld code.'
      : `${plural(count, 'step')}${steps.scope === 'function body' ? ' in the function body' : ''}.`)
    : steps.status === 'none' ? (steps.note || 'The expression is not a let … in: it is a single value or call, so the editor lists no steps.')
    : steps.status === 'not recorded' ? 'This document was generated before step status was recorded.' : steps.note;
  const publicationNote = {included: 'The script below is as written in the file.',
    cleaned: 'The script is included; credentials, personal paths or entered data in it were replaced.',
    withheld: 'Query code is not part of this shared document.'}[q.publication];
  const extraction = q.extraction || {};
  return `<dl class="pq-statuses">
    ${pqStatus('extraction', 'Expression extraction', extraction.status, extraction.note || (extraction.status === 'complete' ? 'The whole expression was read from the file.' : extraction.status === 'not recorded' ? 'This document was generated before extraction status was recorded.' : ''))}
    ${pqStatus('steps', 'Applied Steps', steps.status, stepNote)}
    ${pqStatus('publication', 'Publication', q.publication, publicationNote)}</dl>`;
}
function pqFacts(q) {
  const rows = [];
  const list = (items, empty) => items.length ? items.join(', ') : `<span class="mut">${empty}</span>`;
  if (q.origin === 'table') {
    const partitions = q.partitions || [];
    // Partition names matter when a table has several; a single partition is the table itself.
    rows.push(['Loads table', `${has.model && q.table ? tblLink(q.table) : esc(q.table || '')}${partitions.length > 1 || q.name !== q.table
      ? ` <span class="mut">${partitions.length === 1 ? 'partition' : 'partitions'} ${partitions.map(esc).join(', ')}</span>` : ''}${(q.storageModes || []).length ? ` <span class="badge b-other">${esc(q.storageModes.join(', '))}</span>` : ''}`]);
  }
  if (q.origin) rows.push(['Used by', list((q.usedBy || []).map(t => has.model ? tblLink(t) : esc(t)),
    q.origin === 'table' ? 'No other table reads this query' : 'No table reads this query')]);
  rows.push(['Load status', `<span class="badge ${q.load === 'loaded' ? 'b-direct' : q.load === 'not loaded' ? 'b-info' : 'b-warn'}">${esc(q.load || 'unknown')}</span> <span class="mut">${
    q.load === 'loaded' ? 'Its result is a table in the model.' : q.load === 'not loaded' ? 'It is not a table in the model; other queries may read it.'
      : 'The file does not say whether this query is loaded to the model.'}</span>`]);
  if (q.origin) {
    rows.push(['Upstream queries', list((q.upstream || []).map(queryLink).filter(Boolean), 'Reads no other query')]);
    if ((q.referencedBy || []).length) rows.push(['Read by queries', list(q.referencedBy.map(queryLink).filter(Boolean), '')]);
    rows.push(['External sources', (q.sources || []).length ? q.sources.map(s => `<span class="ref">${esc(s)}</span>`).join(' ')
      : `<span class="mut">${q.publication === 'withheld' && !PQ.recorded ? 'Not recorded' : 'None identified in this query'}</span>`]);
  }
  if (q.group) rows.push(['Query folder', esc(q.group)]);
  if (q.resultType && q.resultType !== 'Table') rows.push(['Result when last refreshed', esc(q.resultType)]);
  if (q.alsoShared) rows.push(['Also recorded as', 'a shared expression with the same text; listed once']);
  return `<table class="t pq-facts"><tbody>${rows.map(([k, v]) => `<tr><th scope="row">${k}</th><td>${v}</td></tr>`).join('')}</tbody></table>`;
}
// M as written, with comments, text and keywords tinted. Every piece is escaped; nothing is interpreted.
function hlM(code) {
  const piece = /(\/\/[^\n]*|\/\*[\s\S]*?\*\/)|(#"(?:[^"]|"")*"|"(?:[^"]|"")*")|\b(let|in|each|if|then|else|try|otherwise|error|meta|type|as|is|and|or|not|true|false|null|section|shared)\b/g;
  let out = '', last = 0, m;
  while ((m = piece.exec(code))) {
    out += esc(code.slice(last, m.index));
    out += m[1] ? `<span class="c">${esc(m[1])}</span>` : m[2] ? `<span class="${m[2][0] === '#' ? 'r' : 's'}">${esc(m[2])}</span>` : `<span class="k">${m[3]}</span>`;
    last = m.index + m[0].length;
  }
  return out + esc(code.slice(last));
}
function pqSteps(q) {
  const steps = q.steps || {}, items = steps.items || [];
  if (q.publication === 'withheld') return `<p class="mut">Step names are part of the query code, which this shared document withholds.</p>`;
  if (steps.status === 'parsed') return `${steps.scope === 'function body' ? '<p class="mut">This query is a function; these are the steps of its body.</p>' : ''}
    <ol class="pq-steps">${items.map(s => `<li><span class="pq-step-name">${esc(s.name)}</span></li>`).join('')}</ol>`;
  if (steps.status === 'unsupported') return `<p class="mut">${esc(steps.note || 'The steps could not be read.')} The script below is shown as written.</p>`;
  return `<p class="mut">No top-level Applied Steps: ${esc(steps.note || 'the expression is a single value or call rather than a let … in.')}</p>`;
}
function pqDetail(q) {
  if (!q) return '';
  const code = pqCode(q), withheld = q.publication === 'withheld';
  const lines = code ? code.split('\n').length : 0;
  return `<header class="pq-head"><h2 id="pq-title" tabindex="-1">${esc(q.name)}</h2>
      <span class="badge b-other">${pqKindName[q.kind] || 'Query'}</span>
      ${q.origin === 'shared' ? '<span class="badge b-info">shared query</span>' : q.origin === 'table' ? '<span class="badge b-dimension">table query</span>' : ''}</header>
    ${q.description ? `<p class="pq-desc">${esc(q.description)}</p>` : ''}
    ${pqFacts(q)}
    <h3>Status</h3>${pqStatuses(q)}
    <h3>Applied Steps</h3>${pqSteps(q)}
    <h3>M script</h3>
    ${withheld ? `<p class="mut">Query code is withheld in this shared document. The local document generated from the same file holds the full script.</p>`
      : code.trim() ? `<details class="pq-code" id="pq-code"><summary>Full M script <span class="mut" style="font-weight:400">${plural(lines, 'line')}, as written${q.publication === 'cleaned' ? ' apart from the replaced values' : ''}</span></summary><div class="body"><pre class="code">${hlM(code)}</pre></div></details>`
      : `<p class="mut">No M expression was read for this query.</p>`}`;
}
function selectQuery(id, focus) {
  if (!PQ_BY_ID.has(id)) return false;
  pqSelected = id;
  const detail = document.getElementById('pq-detail');
  if (!detail) return false;
  detail.innerHTML = pqDetail(PQ_BY_ID.get(id));
  document.querySelectorAll('.pq-item').forEach(b => b.setAttribute('aria-current', String(b.id === pqItemId(id))));
  if (focus !== false && typeof recordObject === 'function') { recordObject(id); document.title = PQ_BY_ID.get(id).name + ' · ' + DATA.title; }
  return true;
}
function pqFilter() {
  const box = document.getElementById('pq-filter');
  if (!box) return;
  const q = box.value.trim().toLowerCase();
  let shown = 0;
  document.querySelectorAll('.pq-item').forEach(b => {
    const on = !q || (b.dataset.name || '').includes(q);
    b.parentElement.style.display = on ? '' : 'none';
    if (on) shown++;
  });
  document.querySelectorAll('.pq-tree .pq-list').forEach(ul => {
    const any = Array.from(ul.children).some(li => li.style.display !== 'none');
    ul.style.display = any ? '' : 'none';
    const head = ul.previousElementSibling;
    if (head && head.classList && head.classList.contains('pq-folder')) head.style.display = any ? '' : 'none';
  });
  const status = document.getElementById('pq-filter-status');
  if (status) status.textContent = !q ? '' : shown ? `${plural(shown, 'query', 'queries')} shown` : 'No matching queries.';
}
