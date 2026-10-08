// Table kinds, fx and the calculation tabs in the actual generated script, run with a minimal DOM adapter.
// Layout and the fx marker in every view are checked in a real browser by browser_model_kinds.cjs.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[2],'utf8');
const nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{id,value:'',innerHTML:'',textContent:'',hidden:false,style:{},
  classList:{add(){},remove(){}},setAttribute(k,v){this['@'+k]=v;},focus(){}});return nodes.get(id);};
const browserWindow={scrollTo(){},location:{hash:''},addEventListener(){},history:{pushState(){},replaceState(){}}};
const context=vm.createContext({console,setTimeout:()=>0,clearTimeout(){},document:{getElementById:node,querySelectorAll:()=>[],
  createElement:()=>({}),body:{appendChild(){}}},window:browserWindow});
vm.runInContext(html.match(/<script>([\s\S]*)<\/script>/)[1],context);
const run=s=>vm.runInContext(s,context);
const same=(a,b)=>assert.equal(JSON.stringify(a),JSON.stringify(b));
const text=s=>s.replace(/<[^>]+>/g,' ').replace(/&quot;/g,'"').replace(/&amp;/g,'&').replace(/&#39;/g,"'").replace(/\s+/g,' ').trim();
const show=tab=>{run(`switchTab(${JSON.stringify(tab)})`);assert.equal(run('activeTab'),tab);return node('main').innerHTML;};

// One kind per table, whether it was recorded in the payload or worked out when the page was rendered.
same(run('M.tables.map(t=>[t.name,definedLabel(t)])'),[['Sales','Power Query'],['Budget','SQL query'],['Lake','Entity'],
  ['Calendar','Calculated table'],['LocalDateTable_7f','Automatic date table'],['DateTableTemplate_9a','Automatic date table'],
  ['LocalDateTable_lookalike','Calculated table'],['Time Intelligence','Calculation group'],['Mixed','Other (m, calculated)'],
  ['Refreshed','Other (policyRange)'],['Empty','Other (no partition)']]);
assert.ok(run('M.tables.every(t=>DEFINED_KINDS.includes(definedBy(t).kind))'));
const tables=show('tables');
for(const [name,kind] of run('M.tables.map(t=>[t.name,definedLabel(t)])')){
  const card=tables.split('<details id="tbl-').find(c=>c.includes(`data-table="${name}"`));
  assert.equal((card.match(/class="defined"/g)||[]).length,1,name+' shows one kind');
  assert.match(text(card.slice(0,card.indexOf('</summary>'))),new RegExp('defined by '+kind.replace(/[()]/g,'\\$&')));
  assert.match(card,new RegExp('<b>Defined by '+run(`definedBy(M.tables.find(t=>t.name===${JSON.stringify(name)})).kind`)+'\\.</b>'));
}
// The role label is still there, unchanged, beside the kind.
assert.match(tables,/data-table="Calendar">\s*<summary>Calendar <span class="badge b-date">date dimension<\/span> <span class="defined"/);
assert.match(tables,/data-table="Sales">\s*<summary>Sales <span class="badge b-fact">fact<\/span> <span class="defined"/);
// fx on calculated columns only; a calculated table's own column says where it comes from instead.
assert.ok(run("isCalcColumn('Sales','Double')&&isCalcColumn('Calendar','Year')&&!isCalcColumn('Calendar','Date')&&!isCalcColumn('Sales','Amount')"));
assert.equal((tables.match(/class="fx-link"/g)||[]).length,2,'two calculated columns, two fx markers in Table view');
assert.match(tables,/id="col-[^"]+" data-column="Year"[\s\S]{0,200}?class="fx-link"/);
// Text that names a calculated column gets the marker; other text does not.
assert.equal(run("fxOfRef('Sales[Double]')"),' '+run('FX'));assert.equal(run("fxOfRef('Sales[Amount]')"),'');
assert.equal(run("fxInText('Calculated column Sales[Double]')"),' '+run('FX'));
assert.equal(run("fxInText('Measure Sales[Total] reads Sales[Amount]')"),'');
assert.equal(run("fxInText('Other[Double]')"),'','the same column name in another table is not a calculated column');
assert.equal(run("fxText('Calendar','Year')"),' (fx)');assert.equal(run("fxText('Calendar','Date')"),'');

// DAX query view tabs: each lists exactly its own kind, and the tab count says how many.
const count=id=>run(`TABS.find(t=>t.id===${JSON.stringify(id)}).count()`);
same(['calc-columns','calc-tables','calc-groups'].map(id=>run(`TABS.find(t=>t.id===${JSON.stringify(id)}).label`)),
  ['Calculated Columns','Calculated Tables','Calculation Groups']);
const columns=show('calc-columns');
same([...columns.matchAll(/<span class="mea-name">([^<]*?) <abbr class="fx"/g)].map(m=>m[1]),['Double','Year']);
assert.equal(count('calc-columns'),2);
assert.match(columns,/<pre class="code"[^>]*>[\s\S]*?Amount[\s\S]*?\* 2/);
const calcTables=show('calc-tables');
same([...calcTables.matchAll(/<details class="measure calc-table" id="[^"]+" open>\s*<summary><span class="mea-name">([^<]*)</g)].map(m=>m[1]),
  ['Calendar','LocalDateTable_lookalike']);
assert.equal(count('calc-tables'),2);
assert.match(text(calcTables),/2 automatic date tables that Power BI creates for date columns are not listed here; they are at the end of Table view/);
assert.ok(!calcTables.includes('LocalDateTable_7f')&&!calcTables.includes('DateTableTemplate_9a'),'automatic date tables are not calculated tables');
assert.match(text(calcTables),/Columns from the expression Date Calculated columns added Year fx/);
const groups=show('calc-groups');
same([...groups.matchAll(/<summary><span class="mea-name">([^<]*)</g)].map(m=>m[1]),['Time Intelligence']);
assert.equal(count('calc-groups'),1);
assert.match(text(groups),/precedence 5 .*1 calculation item/);
assert.match(groups,/<h3 class="calc-item">YTD<\/h3>/);
// Counts and lists cannot disagree: they are the same function.
assert.equal(count('calc-columns'),run('calcColumns().length'));
assert.equal(count('calc-tables'),run("tablesDefinedBy('Calculated table').length"));
assert.equal(count('measures'),run('M.measures.length'));
// The finder tags every table and column with its kind.
same(run("['Calendar','LocalDateTable_7f','Time Intelligence','Sales'].map(n=>objectFor('table',n).kind)"),
  ['calculated table','automatic date table','calculation group','table']);
same(run("[objectFor('column','Sales','Double').kind,objectFor('column','Calendar','Date').kind]"),['calculated column','column']);
console.log('Table kinds, fx and calculation tabs: checks passed for '+run('M.tables.length')+' tables.');
