// Exercise the actual generated HTML script with a minimal DOM adapter.
// This checks filtering, sort state, escaping and CSV data, not visual layout.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[2], 'utf8');
const nodes = new Map();
const node = id => {
  if (!nodes.has(id)) nodes.set(id, {value:'', innerHTML:'', textContent:'',
    classList:{add(){},remove(){}}, setAttribute(){}});
  return nodes.get(id);
};
const context = vm.createContext({console, setTimeout, document:{
  getElementById:node, querySelectorAll:()=>[]}, window:{scrollTo(){}}});
vm.runInContext(html.match(/<script>([\s\S]*)<\/script>/)[1], context);
const evaluate = text => vm.runInContext(text, context);
assert.match(node('main').innerHTML, /Data sources at a glance/);  // opens on Overview
evaluate("switchTab('columns')");
assert.match(node('main').innerHTML, /Which columns are used/);
assert.equal(evaluate('visibleColumns.length'), 11);
node('column-used').value = 'Yes'; evaluate('filterColumns()');
assert.equal(evaluate('visibleColumns.length'), 3);
node('column-used').value = ''; node('column-assessment').value = 'Deletion candidate'; evaluate('filterColumns()');
assert.equal(evaluate('visibleColumns.length'), 3);
node('column-assessment').value = ''; evaluate("pageScope = 'p2'; filterColumns()");
assert.equal(evaluate('visibleColumns.length'), 1);
assert.equal(evaluate('visibleColumns[0].column'), 'Amount');
evaluate("pageScope = '*'"); node('column-search').value = 'NetAmount'; evaluate('filterColumns()');
assert.equal(evaluate('visibleColumns.length'), 2);
evaluate("sortColumns('column')"); assert.equal(evaluate('columnSort.direction'), -1);
node('column-search').value = 'no-such-column-xyz'; evaluate('filterColumns()');
assert.match(node('column-rows').innerHTML, /No columns match/);
node('column-search').value = ''; evaluate('filterColumns()');
evaluate(`DATA.columns.rows[0].column = '<img src=x onerror=alert(1)>';
  DATA.columns.rows[0].page = '=1+1'; filterColumns();`);
assert.ok(!node('column-rows').innerHTML.includes('<img src=x'));
assert.ok(node('column-rows').innerHTML.includes('&lt;img'));
const csv = evaluate('columnCsv(DATA.columns.rows)');
assert.ok(csv.startsWith('\uFEFF'));
assert.ok(csv.includes('"\'=1+1"'));
fs.writeFileSync(process.argv[3], csv);
evaluate("switchTab('tables')");
assert.match(node('main').innerHTML, /Report → page → table → source/);
assert.match(node('main').innerHTML, /dbo\.Orders/);
assert.match(node('main').innerHTML, /<td>server<\/td><td>db<\/td>/);
assert.equal(evaluate("sourceRows('Orders').length"), 3);
assert.equal(evaluate("sourceRows('Orders','p2').length"), 1);
assert.equal(evaluate("sourceRows('Orders','p2')[0].pageId"), 'p2');
assert.equal(evaluate("sourceRows('no-such-source').length"), 0);
evaluate(`DATA.tableSources[0].query = '<script>alert(1)</script>';
  DATA.tableSources[0].queryKind = 'SQL'; switchTab('tables');`);
assert.ok(!node('main').innerHTML.includes('<script>alert(1)</script>'));
assert.match(node('main').innerHTML, /&lt;script&gt;/);
assert.ok(evaluate("inventoryCsv(sourceRows('Orders'), sourceCsvFields)").includes('"Source object"'));
assert.ok(evaluate("inventoryCsv(sourceRows('Orders'), sourceCsvFields)").includes('"Page ID"'));
evaluate("switchTab('pages')");
assert.match(node('main').innerHTML, /Sales \/ Same \/ page \[p1\]/);
assert.match(node('main').innerHTML, /Sales \/ Same \/ page \[p2\]/);
evaluate('drawLineage()');
assert.match(node('lineage-board').innerHTML, /id="ln-p-70_31"/);
assert.match(node('lineage-board').innerHTML, /id="ln-p-70_32"/);
assert.ok(evaluate("pageKey('p-b') !== pageKey('p_b')"));
console.log('HTML script checks passed (filters, sorting, escaping, CSV).');
