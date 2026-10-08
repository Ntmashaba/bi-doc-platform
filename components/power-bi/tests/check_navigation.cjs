// Object index, finder and routing in the actual generated script, run with a minimal DOM adapter.
// Layout, scrolling and focus are checked in a real browser by browser_navigation.cjs.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[2],'utf8');
const nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{id,value:'',innerHTML:'',textContent:'',hidden:false,style:{},
  classList:{add(){},remove(){}},setAttribute(k,v){this['@'+k]=v;},focus(){}});return nodes.get(id);};
const listeners={};
const browserWindow={scrollTo(){},location:{hash:''},addEventListener(name,fn){listeners[name]=fn;},history:{
  pushState(_s,_t,hash){browserWindow.location.hash=hash;},replaceState(_s,_t,hash){browserWindow.location.hash=hash;}}};
const context=vm.createContext({console,setTimeout:()=>0,clearTimeout(){},document:{getElementById:node,querySelectorAll:()=>[],
  createElement:()=>({}),body:{appendChild(){}}},window:browserWindow});
vm.runInContext(html.match(/<script>([\s\S]*)<\/script>/)[1],context);
const run=s=>vm.runInContext(s,context);
const same=(a,b)=>assert.equal(JSON.stringify(a),JSON.stringify(b));

// The index is the embedded JSON, and every entry is a distinct, addressable object.
const index=run('DERIVED.search');
assert.equal(run('OBJECTS.length'),index.items.length);
assert.equal(new Set(run('OBJECTS.map(o=>o.id)')).size,index.items.length,'object ids are unique');
assert.ok(run("OBJECTS.every(o=>o.name.length>0)"),'every object has a name to show');
assert.ok(run("OBJECTS.every(o=>{const h=homeOf(o);return h&&TABS.some(t=>t.id===h.tab&&t.avail)})"),'every indexed object has a view that is available');
// Browse: with no text, everything, grouped by kind in the index's order and sorted within a kind.
const browse=run("searchObjects('')");
assert.equal(browse.total,index.items.length);
same(browse.groups.map(g=>g.kind),index.kinds.filter(k=>browse.groups.some(g=>g.kind===k)));
for(const g of browse.groups){
  const names=g.items.map(o=>o.name.toLowerCase());
  if(!['visual','data source'].includes(g.kind)) same(names,[...names].sort((a,b)=>a<b?-1:a>b?1:0));
}
if(run('has.model')){
  // Typing narrows, case-insensitively, by substring of the name.
  const counts=['a','am','amo','amou','amount'].map(q=>run(`searchObjects(${JSON.stringify(q)}).total`));
  for(let i=1;i<counts.length;i++) assert.ok(counts[i]<=counts[i-1],'each keystroke narrows: '+counts);
  assert.ok(counts[counts.length-1]>=1);
  assert.equal(run("searchObjects('AMOUNT').total"),run("searchObjects('amount').total"));
  assert.equal(run("searchObjects('mount').total"),run("searchObjects('amount').total"),'a match may start inside the name');
  assert.equal(run("searchObjects('no-such-object-xyz').total"),0);
  // A result carries name, kind and parent; a DAX column says "calculated column", never "column".
  const double=run("searchObjects('Double').groups.flatMap(g=>g.items).map(o=>[o.name,o.kind,o.parent])");
  same(double,[['Double','calculated column','Sales']]);
  same(run("searchObjects('Amount').groups.flatMap(g=>g.items).filter(o=>o.name==='Amount').map(o=>[o.kind,o.parent])"),[['column','Sales']]);
  // The qualified name finds a column through its table.
  assert.ok(run("searchObjects('sales[amo').groups.flatMap(g=>g.items).some(o=>o.name==='Amount')"));
  // Best match first within a kind: the whole name, then its start, then inside.
  const ranked=run("searchObjects('sales').groups.find(g=>g.kind==='table').items.map(o=>o.name)");
  assert.equal(ranked[0],'Sales');
  // References used by the rest of the page resolve through the same index.
  assert.equal(run("objectFor('table','Sales').kind"),'table');
  assert.equal(run("objectFor('column','Sales','Double').kind"),'calculated column');
  assert.equal(run("objectFor('measure','Total').parent"),'Sales');
  assert.equal(run("objectFor('table','No such table')"),undefined);
  // Selecting opens the view the object lives in and records an object link.
  assert.equal(run("goRef('measure','Total')"),true);
  assert.equal(run('activeTab'),'measures');
  assert.equal(browserWindow.location.hash,'#o/'+encodeURIComponent(run("objectFor('measure','Total').id")));
  assert.equal(run("goRef('table','No such table')"),false);
  assert.equal(run('activeTab'),'measures','an unknown object changes nothing');
  // An object link routes to the object (a reload, Back and Forward all arrive through routeReport).
  const column=run("objectFor('column','Sales','Amount').id");
  browserWindow.location.hash='#o/'+encodeURIComponent(column);listeners.popstate();
  assert.equal(run('activeTab'),'tables');
  browserWindow.location.hash='#measures';listeners.popstate();assert.equal(run('activeTab'),'measures');
  browserWindow.location.hash='#o/'+encodeURIComponent('pbi:table:name:Gone');listeners.popstate();
  assert.equal(run('activeTab'),'overview');
  assert.match(node('finder-note').textContent,/does not contain/);
  browserWindow.location.hash='#o/%E0%A4%A';listeners.popstate();assert.equal(run('activeTab'),'overview','a malformed link is not an error');
  // A page selection that hides the object is undone when the object is selected.
  if(run('has.report')){
    run("setPageScope('p2')");
    assert.equal(run("goRef('visual','p1','v1')"),true);
    assert.equal(run('activeTab'),'pages');
  }
  // Tables search: a table is kept for its name, a column or a source field, and matching columns are named.
  run("pageScope='*';switchTab('tables')");
  same(run("tableSearch('unus').tables"),[{name:'Sales',columns:['Unused'],named:false}]);
  assert.ok(run("tableSearch('unus').rows.length")>0&&run("tableSearch('unus').rows.every(r=>r.table==='Sales')"));
  same(run("tableSearch('UNUS').tables"),run("tableSearch('unus').tables"));
  same(run("tableSearch('dim').tables.map(t=>[t.name,t.named])"),[['Dim',true]]);
  assert.ok(run("tableSearch('orders').tables.some(t=>t.name==='Sales'&&!t.named&&!t.columns.length)"),'a source field still finds its table');
  assert.equal(run("tableSearch('').tables.length"),run('scopedTables().length'));
  same(run("tableSearch('no-such-thing-xyz')"),{query:'no-such-thing-xyz',tables:[],rows:[]});
  node('table-search').value='no-such-thing-xyz';run("fTbl(document.getElementById('table-search').value)");
  assert.equal(node('table-search-status').textContent,'No matching tables or columns.');
  assert.equal(node('tbl-results').hidden,true);
  run("fTbl('')");assert.equal(node('table-search-status').textContent,'');assert.equal(node('tbl-results').hidden,false);
  // The finder renders what searchObjects returns, with a count and an explicit empty state.
  node('finder-input').value='Double';run('finderInput()');
  assert.match(node('finder-list').innerHTML,/role="option"/);
  assert.match(node('finder-list').innerHTML,/calculated column/);
  assert.equal(node('finder-count').textContent,'1 result');
  assert.equal(node('finder-panel').hidden,false);
  node('finder-input').value='no-such-object-xyz';run('finderInput()');
  assert.match(node('finder-list').innerHTML,/No objects match/);
  assert.equal(node('finder-count').textContent,'No results');
  node('finder-input').value='';run('finderInput()');
  assert.equal(node('finder-count').textContent,index.items.length.toLocaleString()+' objects');
}
// Names the page gives an object are the ones the index shows.
if(run('has.report')){
  assert.ok(run("OBJECTS.filter(o=>o.kind==='visual').every(o=>{const p=R.pages.find(p=>p.id===o.a);return o.name===visualName(o.a,p.visuals.find(v=>v.id===o.b))})"));
  assert.equal(run("OBJECTS.filter(o=>o.kind==='page').length"),run('R.pages.length'));
}
if(run('has.model')) assert.ok(run("OBJECTS.filter(o=>o.kind==='data source'&&o.key).every(o=>sourceGroups().some(g=>g.key===o.key&&g.name===o.name))"));
// Names with markup stay text in the list.
if(index.items.length){
  run("OBJECTS[0].name='<img src=x onerror=alert(1)>';OBJECTS[0].lower=OBJECTS[0].name.toLowerCase()");
  node('finder-input').value='<img';run('finderInput()');
  assert.match(node('finder-list').innerHTML,/&lt;img/);
  assert.ok(!node('finder-list').innerHTML.includes('<img src=x'));
}
console.log(run('DATA.title')+': object index, finder, routing and Tables search checks passed ('+index.items.length+' objects).');
