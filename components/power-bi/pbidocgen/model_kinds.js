/* How each table is defined, and which columns are calculated columns.

   "Defined by" is one kind per table, read from the table's own metadata when the document was generated
   (table_kinds.py): Power Query, SQL query, Entity, Calculated table, Automatic date table, Calculation group or
   Other. It is a different statement from the table's role (fact, dimension, date dimension).
   A calculated column carries the fx marker wherever its name is shown. */
const TABLE_KINDS = (typeof DERIVED !== 'undefined' && DERIVED && DERIVED.tableKinds) || {};
const DEFINED_KINDS = ['Power Query', 'SQL query', 'Entity', 'Calculated table', 'Automatic date table', 'Calculation group', 'Other'];
const definedBy = t => (t && t.definedBy) || TABLE_KINDS[t && t.name] || {kind: 'Other', raw: 'not recorded'};
const definedLabel = t => { const d = definedBy(t); return d.kind === 'Other' && d.raw ? `Other (${d.raw})` : d.kind; };
const tablesDefinedBy = kind => (M?.tables || []).filter(t => definedBy(t).kind === kind);
const isAutoDate = t => definedBy(t).kind === 'Automatic date table';
const CALC_COLUMN_KEYS = new Set((M?.tables || []).flatMap(t => t.columns.filter(c => c.isCalculated).map(c => t.name + '\u0001' + c.name)));
const isCalcColumn = (table, column) => CALC_COLUMN_KEYS.has(table + '\u0001' + column);
const FX = '<abbr class="fx" title="Calculated column: a DAX expression evaluated for each row">fx</abbr>';
// After a column name in markup; '' for any other column.
const fx = (table, column) => isCalcColumn(table, column) ? ' ' + FX : '';
// Where markup cannot go (a drop-down option, a tooltip): the same marker as text.
const fxText = (table, column) => isCalcColumn(table, column) ? ' (fx)' : '';
// A reference written as text: Sales[Double].
const refParts = ref => { const m = /^'?(.*?)'?\[(.*)\]$/.exec(String(ref ?? '')); return m && m[1] ? [m[1].replace(/''/g, "'"), m[2]] : null; };
const fxOfRef = ref => { const p = refParts(ref); return p ? fx(p[0], p[1]) : ''; };
const fxTextOfRef = ref => { const p = refParts(ref); return p ? fxText(p[0], p[1]) : ''; };
// A sentence written by the analysis that names columns as Table[Column] ("Calculated column Sales[Double]"):
// the marker, once, when one of them is a calculated column.
const CALC_TABLES_BY_COLUMN = new Map();
for (const key of CALC_COLUMN_KEYS) { const [t, c] = key.split('\u0001'); if (!CALC_TABLES_BY_COLUMN.has(c)) CALC_TABLES_BY_COLUMN.set(c, []); CALC_TABLES_BY_COLUMN.get(c).push(t); }
function namesCalcColumn(text) {
  if (!CALC_COLUMN_KEYS.size) return false;
  const s = String(text ?? '');
  for (const m of s.matchAll(/\[([^\[\]]+)\]/g)) {
    const tables = CALC_TABLES_BY_COLUMN.get(m[1]);
    if (!tables) continue;
    const before = s.slice(0, m.index);
    if (tables.some(t => before.endsWith(t) || before.endsWith("'" + t.replace(/'/g, "''") + "'"))) return true;
  }
  return false;
}
const fxInText = text => namesCalcColumn(text) ? ' ' + FX : '';
// A node of the dependency graph: its label, marked when it is a calculated column.
const nodeHtml = n => n ? esc(n.label) + (n.kind === 'column' ? fx(n.table, n.name) : '') : '';
const calcColumns = () => (M?.tables || []).flatMap(t => t.columns.filter(c => c.isCalculated).map(c => ({table: t, column: c})));
const calcColumnAnchor = (table, column) => 'cc-' + slug(table) + '-' + slug(column);
const calcTableAnchor = table => 'ct-' + slug(table);
const calcGroupAnchor = table => 'cg-' + slug(table);
function goCalc(tab, anchor) {
  switchTab(tab, true, true);
  nextFrame(() => { const el = document.getElementById(anchor); if (!el) return; openContainers(el); el.open = true;
    el.scrollIntoView({block: 'center', behavior: 'instant'}); markObject(el); focusObject(el); });
}

/* ------------------------------------------------------------ DAX query view: the calculation tabs */
const daxPreview = e => { const one = String(e || '').replace(/\s+/g, ' ').trim(); return one.length > 90 ? one.slice(0, 89) + '…' : one; };
function rCalcColumns() {
  const all = calcColumns();
  const head = `<h1>Calculated Columns</h1>
    <p class="sub">Columns defined by a DAX expression that is evaluated for each row, marked ${FX} wherever they appear in this document. Each is classified from its own metadata, not from the table it belongs to.</p>`;
  if (!all.length) return `${head}<div class="empty"><b>This model has no calculated columns.</b></div>`;
  const card = ({table: t, column: c}) => `<details class="measure calc-col" id="${calcColumnAnchor(t.name, c.name)}" data-search="${esc([c.name, t.name, c.expression, c.description, c.displayFolder].join(' ').toLowerCase())}">
      <summary><span class="mea-name">${esc(c.name)} ${FX}</span>
        ${c.isHidden ? '<span class="badge b-hidden">hidden</span>' : ''}
        ${has.linked && c.reportUsage ? usageBadge(c.reportUsage) : ''}
        <span class="mut" style="font-weight:400;margin-left:auto">${esc(t.name)}</span>
        <span class="mea-preview">${esc(daxPreview(c.expression))}</span></summary>
      <div class="body">
        ${c.description ? `<p class="mut" style="margin:.5rem 0">${esc(c.description)}</p>` : ''}
        <pre class="code" style="margin-top:.6rem">${hlDAX(c.expression || '')}</pre>
        <table class="t" style="margin-top:.6rem"><tbody>
          <tr><td class="mut" style="width:170px">Table</td><td>${tblLink(t.name)} <span class="mut">defined by ${esc(definedLabel(t))}</span></td></tr>
          <tr><td class="mut">Column</td><td><button class="xl" onclick="${action('goRef', 'column', t.name, c.name)}">Open ${esc(c.name)} in Table view</button></td></tr>
          <tr><td class="mut">Data type</td><td>${esc(c.dataType || '—')}</td></tr>
          <tr><td class="mut">Depends on</td><td>${calcColumnReads(t.name, c).join(' ') || '—'}</td></tr>
          ${c.formatString ? `<tr><td class="mut">Format</td><td><span class="ref">${esc(c.formatString)}</span></td></tr>` : ''}
          ${c.sortByColumn ? `<tr><td class="mut">Sorted by</td><td><span class="ref">${esc(c.sortByColumn)}</span>${fx(t.name, c.sortByColumn)}</td></tr>` : ''}
        </tbody></table>
      </div></details>`;
  const tables = [...new Set(all.map(x => x.table.name))];
  const grouped = tables.length > 1
    ? tables.map(name => `<section class="mea-group"><h2>${esc(name)} <span class="mut">${all.filter(x => x.table.name === name).length}</span></h2>${all.filter(x => x.table.name === name).map(card).join('')}</section>`).join('')
    : all.map(card).join('');
  return `${head}
    <input id="calc-column-search" class="search" placeholder="Search names, tables or DAX…" aria-label="Search calculated columns" oninput="fCalcColumns(this.value)">
    <p id="calc-column-status" class="mut" role="status" aria-live="polite"></p>
    <div id="calc-column-list">${grouped}</div>`;
}
// What a calculated column reads: from the dependency graph (which binds an unqualified [Column] to its own
// table), or from the references read off its expression when this document has no graph.
function calcColumnReads(table, c) {
  const id = nodeId('c', table, c.name);
  const nodes = graph.edges.filter(e => e.dependent === id).map(e => graphNodes.get(e.dependency)).filter(Boolean);
  if (nodes.length) return nodes.map(n => `<span class="ref">${esc(n.label)}</span>${n.kind === 'column' ? fx(n.table, n.name) : ''}`);
  return (c.dependsOn || []).map(d => `<span class="ref">${esc(d)}</span>${fxOfRef(d)}`);
}
function fCalcColumns(q) {
  q = String(q || '').trim().toLowerCase();
  let shown = 0;
  document.querySelectorAll('#calc-column-list details.calc-col').forEach(d => { const on = (d.dataset.search || '').includes(q); d.style.display = on ? '' : 'none'; if (on) shown++; });
  document.querySelectorAll('#calc-column-list .mea-group').forEach(g => {
    g.style.display = [...g.querySelectorAll('details.calc-col')].some(d => d.style.display !== 'none') ? '' : 'none';
  });
  const status = document.getElementById('calc-column-status');
  if (status) status.textContent = !q ? '' : shown ? `${plural(shown, 'calculated column')} shown` : 'No matching calculated columns.';
}
function rCalcTables() {
  const tables = tablesDefinedBy('Calculated table'), auto = tablesDefinedBy('Automatic date table');
  const head = `<h1>Calculated Tables</h1>
    <p class="sub">Tables whose rows come from a DAX expression. A table is listed here because every one of its partitions is a DAX expression, whatever its name.</p>`;
  const autoNote = auto.length ? `<p class="mut" style="margin-top:1rem">${plural(auto.length, 'automatic date table')} that Power BI creates for date columns ${auto.length === 1 ? 'is' : 'are'} not listed here; ${auto.length === 1 ? 'it is' : 'they are'} in <button class="xl" onclick="switchTab('tables')">Table view</button>.</p>` : '';
  if (!tables.length) return `${head}<div class="empty"><b>This model has no calculated tables.</b></div>${autoNote}`;
  return `${head}${tables.map(t => {
    const own = t.columns.filter(c => c.isCalculated), fromTable = t.columns.filter(c => !c.isCalculated);
    return `<details class="measure calc-table" id="${calcTableAnchor(t.name)}" open>
      <summary><span class="mea-name">${esc(t.name)}</span> ${typeBadge(t.tableType)} ${t.isHidden ? '<span class="badge b-hidden">hidden</span>' : ''}
        <span class="mut" style="font-weight:400;margin-left:auto">${plural(t.columns.length, 'column')} · ${plural(t.measures.length, 'measure')}</span></summary>
      <div class="body">
        ${t.description ? `<p class="mut" style="margin:.5rem 0">${esc(t.description)}</p>` : ''}
        ${t.partitions.map(p => `<pre class="code" style="margin-top:.6rem">${hlDAX(p.expression || '')}</pre>`).join('')}
        <table class="t" style="margin-top:.6rem"><tbody>
          <tr><td class="mut" style="width:170px">Table</td><td>${tblLink(t.name)}</td></tr>
          <tr><td class="mut">Columns from the expression</td><td>${fromTable.map(c => `<span class="ref">${esc(c.name)}</span>`).join(' ') || '—'}</td></tr>
          ${own.length ? `<tr><td class="mut">Calculated columns added</td><td>${own.map(c => `<span class="ref">${esc(c.name)}</span> ${FX}`).join(' ')}</td></tr>` : ''}
        </tbody></table>
      </div></details>`; }).join('')}${autoNote}`;
}
function rCalcGroups() {
  const groups = tablesDefinedBy('Calculation group');
  const head = `<h1>Calculation Groups</h1>
    <p class="sub">Tables that hold calculation items: DAX applied to whichever measure is being evaluated.</p>`;
  if (!groups.length) return `${head}<div class="empty"><b>This model has no calculation groups.</b></div>`;
  return `${head}${groups.map(t => {
    const items = [...(t.calculationGroup || [])].map((item, i) => ({item, i}))
      .sort((a, b) => (a.item.ordinal ?? a.i) - (b.item.ordinal ?? b.i) || a.i - b.i).map(x => x.item);
    const precedence = t.calculationGroupPrecedence ?? t.calculationGroupDefinition?.precedence;
    return `<details class="measure calc-group" id="${calcGroupAnchor(t.name)}" open>
      <summary><span class="mea-name">${esc(t.name)}</span>
        ${precedence !== undefined && precedence !== null && precedence !== '' ? `<span class="badge b-other">precedence ${esc(precedence)}</span>` : ''}
        <span class="mut" style="font-weight:400;margin-left:auto">${plural(items.length, 'calculation item')}</span></summary>
      <div class="body">
        ${t.description ? `<p class="mut" style="margin:.5rem 0">${esc(t.description)}</p>` : ''}
        <p class="mut" style="margin:.5rem 0">${tblLink(t.name)} <span class="mut">in Table view</span></p>
        ${items.map(item => `<h3 class="calc-item">${esc(item.name)}</h3>${item.description ? `<p class="mut">${esc(item.description)}</p>` : ''}
          <pre class="code">${hlDAX(item.expression || '')}</pre>
          ${item.formatStringExpression ? `<p class="mut" style="margin:.4rem 0 .2rem">Format string expression</p><pre class="code">${hlDAX(item.formatStringExpression)}</pre>` : ''}`).join('') || '<p class="mut">No calculation items were read for this group.</p>'}
      </div></details>`; }).join('')}`;
}
