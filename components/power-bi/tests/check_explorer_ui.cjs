// Stateful DOM adapter for generated JavaScript. Does not verify browser layout.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[2],'utf8');
const nodes=new Map();let mounted=[];
const decode=s=>s.replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
function node(id){
  if(!nodes.has(id)){
    let content='';
    nodes.set(id,{id,value:'',hidden:false,style:{},classList:{add(){},remove(){}},setAttribute(){},focus(){},querySelectorAll(){return []},
      get innerHTML(){return content;},set innerHTML(s){content=s;if(id==='main') mount(s);}});
  }
  return nodes.get(id);
}
function mount(html){
  mounted=[];
  for(const m of html.matchAll(/<(input|select|details)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){
    const el=node(m[3]);el.value=decode(m[2].match(/\bvalue="([^"]*)"/)?.[1]||'');el.tagName=m[1].toUpperCase();
    if(el.tagName==='SELECT'){
      const body=html.slice(m.index).split('</select>')[0];
      const options=[...body.matchAll(/<option(?: value="([^"]*)")?([^>]*)>([^<]*)<\/option>/g)];
      const o=options.find(x=>x[2].includes('selected'))||options[0];el.value=decode(o?.[1]??o?.[3]??'');
    }
    mounted.push(el);
  }
}
const historyEntries=[], listeners={};
const browserWindow={scrollTo(){},location:{hash:''},addEventListener(name,fn){listeners[name]=fn;},history:{
 pushState(_state,_title,hash){historyEntries.push(hash);browserWindow.location.hash=hash;},
 replaceState(_state,_title,hash){browserWindow.location.hash=hash;}
}};
const context=vm.createContext({console,setTimeout,document:{getElementById:node,
 querySelectorAll:s=>s.startsWith('#main input')?mounted.filter(e=>['INPUT','SELECT'].includes(e.tagName)):[],
 createElement:()=>({}),body:{appendChild(){}}},window:browserWindow});
vm.runInContext(html.match(/<script>([\s\S]*)<\/script>/)[1],context);
const run=s=>vm.runInContext(s,context);
const same=(a,b)=>assert.equal(JSON.stringify(a),JSON.stringify(b));
// Sections have deep links; Back restores the prior view without adding history.
const routeTarget=run("TABS.find(t=>t.avail&&t.id!=='overview').id");
run(`switchTab(${JSON.stringify(routeTarget)})`);assert.equal(browserWindow.location.hash,'#'+routeTarget);
const historyCount=historyEntries.length;
run(`switchTab(${JSON.stringify(routeTarget)})`);assert.equal(historyEntries.length,historyCount);
browserWindow.location.hash='#overview';listeners.popstate();
assert.equal(run('activeTab'),'overview');assert.equal(historyEntries.length,historyCount);
browserWindow.location.hash='#does-not-exist';listeners.popstate();assert.equal(run('activeTab'),'overview');
assert.match(node('nav').innerHTML,/href="pbi-home.html#reports"/);
assert.ok(!run("sectionTabs(sectionOf('compare')).some(t=>t.id==='compare')"));
assert.equal(run("sectionOf('impact').label"),'Impact & usage');
// Every available view must render in every supported extraction mode.
for(const tab of run('TABS.filter(t=>t.avail).map(t=>t.id)')) run(`switchTab(${JSON.stringify(tab)})`);
if(!run('has.model&&has.report')){console.log('Available mode views rendered');process.exit(0);}
// Lineage draws scoped paths once, and does not invent links for decorative pages.
run("switchTab('lineage')");
const lineageTable=run('lineageGraph().tables[0].name');
run(`selectLineage('t',${JSON.stringify(lineageTable)})`);
assert.ok(run('lineageSelection(lineageGraph()).st.every(e=>e.t===lineageFocus.key)'));
assert.ok(run('lineageSelection(lineageGraph()).tp.every(e=>e.t===lineageFocus.key)'));
assert.match(node('lineage-selection').innerHTML,/table details/);
run('zoomLineage(10)');assert.equal(node('lineage-zoom-label').textContent,'250%');
run('resetLineage()');assert.equal(node('lineage-zoom-label').textContent,'100%');
run('savedLineage=L.lineage;L.lineage=[...L.lineage,...L.lineage]');
assert.equal(run('lineageGraph().edgesST.length'),run('new Set(savedLineage.map(r=>JSON.stringify([r.sourceLabel||[r.sourceType,r.server,r.database].filter(Boolean).join(" · ")||"Unknown source",r.table]))).size'));
run("L.lineage=savedLineage;savedTablePages=L.tablePages;L.tablePages=[];drawLineage()");
assert.match(node('lineage-note').innerHTML,/No table-to-page connections detected/);
assert.match(node('lineage-note').innerHTML,/does not mean a table is safe to delete/);
assert.equal(run('lineageGraph().edgesTP.length'),0);
assert.doesNotMatch(node('lineage-board').innerHTML,/var\(--none\)/);
run('L.tablePages=savedTablePages;resetLineage()');
// Relationship focus filters details, preserves connected nodes, and bounds zoom.
run("switchTab('rels')");
assert.match(node('erd').innerHTML,/role="button"/);
const focusName=run('M.relationships[0].fromTable');
run(`focusERD(${JSON.stringify(focusName)})`);
assert.equal(node('erd-focus').value,focusName);
assert.match(node('erd').innerHTML,/aria-pressed="true"/);
assert.match(node('erd-selection').innerHTML,/Showing connections/);
run('zoomERD(10)');assert.equal(node('erd-zoom-label').textContent,'250%');
run('zoomERD(-10)');assert.equal(node('erd-zoom-label').textContent,'50%');
run('resetERD()');assert.equal(node('erd-zoom-label').textContent,'100%');assert.equal(node('erd-focus').value,'');
// Same-column and self relationships remain visible; empty models have an explicit state.
run("savedRels=M.relationships;savedTables=M.tables;M.tables=[{name:'A',tableType:'fact'},{name:'B',tableType:'fact'}];M.relationships=[{fromTable:'A',toTable:'B',isActive:false,crossFilteringBehavior:'bothDirections'},{fromTable:'A',toTable:'A',isActive:true}];drawERD()");
assert.match(node('erd').innerHTML,/stroke="var\(--warn\)"/);assert.match(node('erd').innerHTML,/stroke-dasharray="8 6"/);
assert.ok(!node('erd').innerHTML.includes('NaN'));
run('M.relationships=[];drawERD()');assert.match(node('erd').innerHTML,/No relationships in this model/);
run('M.relationships=savedRels;M.tables=savedTables;resetERD()');
run("switchTab('columns')");node('column-search').value='Amount';run('filterColumns()');
run("setPageScope('p2');switchTab('tables')");assert.equal(node('global-page').value,'p2');
assert.equal(run("sourceRows('Orders').length"),1);
run("switchTab('columns')");assert.equal(node('column-search').value,'Amount');assert.equal(run('visibleColumns.length'),1);
assert.equal(run('visibleColumns[0].pageId'),'p2');
run("switchTab('pages')");assert.ok(!node('main').innerHTML.includes('id="pg-70_31"'));assert.ok(node('main').innerHTML.includes('id="pg-70_32"'));
run("switchTab('matrix');toggleMatrixTable('Sales')");assert.match(node('main').innerHTML,/matrix-column/);assert.ok(!run('rMatrix()').includes('<div class="mut">p1</div>'));assert.equal(run("matrixKinds({kinds:['Possible relationship dependency']}).kinds.length"),0);run('matrixRelOnly=true');assert.equal(run("matrixKinds({kinds:['Possible relationship dependency']}).kinds.length"),1);assert.match(run('rMatrix()'),/id="matrix-rel" checked/);run('matrixRelOnly=false');
assert.match(run("measureVisuals(M.measures.find(m=>m.name==='Total'))"),/inspectVisual/);assert.doesNotMatch(run("measureVisuals(M.measures.find(m=>m.name==='Total'))"),/via/);
assert.match(run("measureVisuals(M.measures.find(m=>m.name==='Base'))"),/via Total/);
run("inspectCell('Sales','Amount','p2')");assert.match(node('inspector').innerHTML,/v1/);
const amount='["c","Sales","Amount"]';
same(run(`impactConsumers(${JSON.stringify(amount)}).map(c=>c.pageId)`),['p2']);
same(run(`impactConsumers(${JSON.stringify(amount)})[0].path.map(id=>graphNodes.get(id).name)`),['Amount','Base','Total']);
run(`inspectNode(${JSON.stringify(amount)})`);assert.match(node('inspector').innerHTML,/Amount.*→.*Base.*→.*Total/);
run("inspectNode(nodeId('m','Sales','Total'))");assert.match(node('inspector').innerHTML,/Reads these fields/);assert.match(node('inspector').innerHTML,/Sales\[Base\]/);
run("inspectVisual('p2','v1')");assert.match(node('inspector').innerHTML,/p2/);assert.match(node('inspector').innerHTML,/Resolved fields/);
// A graph cycle must terminate and preserve shortest paths.
run("reverseGraph.set(nodeId('m','Sales','Total'),[nodeId('m','Sales','Base')])");assert.equal(run(`downstreamPaths(${JSON.stringify(amount)}).size`),4);
run("switchTab('cleanup')");assert.match(node('main').innerHTML,/3 distinct columns/);assert.equal(run('cleanupRows().length'),3);assert.match(node('main').innerHTML,/<h2>Measures<\/h2>/);assert.ok(run("cleanupMeasureRows().every(r=>r.decision==='Deletion candidate')"));
run("setPageScope('');switchTab('columns')");node('column-search').value='';run('filterColumns()');assert.ok(run("visibleColumns.every(r=>r.pageId==='')"));
run("setPageScope('*');switchTab('layout')");assert.match(node('main').innerHTML,/without usable coordinates/);
same(run("layoutGeometry({width:100,height:100,visuals:[{x:-10,y:0,width:20,height:30},{x:null,y:0,width:20,height:30}]}).unplaced.length"),1);
// Same extract produces no changes even if generated time or JSON key order differs.
run("comparison=JSON.parse(JSON.stringify(DATA));comparison.generated='earlier';comparisonName='baseline.json'");assert.equal(run('comparisonRows().length'),0);
run("comparison.model.measures.find(m=>m.name==='Base').expression='SUM(Sales[Amount])*2'");
assert.equal(run('comparisonRows().length'),1);same(run('comparisonRows()[0].pages.map(p=>p.pageId)'),['p1','p2']);
run("setPageScope('p2');switchTab('compare')");assert.ok(!run('rCompare()').includes('Same / page [p1]'));
run("setPageScope('*');comparison.report.pages.push({id:'removed-page',name:'Old page',visuals:[],filters:[]})");assert.ok(run("comparisonRows().some(r=>r.change==='Removed'&&r.kind==='Page')"));
run("setPageScope('removed-page')");assert.equal(run('comparisonRows().length'),1);
assert.throws(()=>run('validateExtract({report:{}})'),/incomplete/);
assert.throws(()=>run('validateExtract([])'),/JSON extract/);
run("comparison.report.pages[2].name='<img src=x onerror=alert(1)>';switchTab('compare')");assert.ok(!node('main').innerHTML.includes('<img src=x'));
console.log('Explorer checks passed: scope, state, paths, layout, cleanup, comparison and escaping.');

// Regression assertions for comparison completeness, identity and state.
run("setPageScope('*')");
assert.ok(run("slug('Sales-US') !== slug('Sales US')"));
assert.throws(()=>run('validateExtract({schemaVersion:999,model:{tables:[],measures:[]}})'),/Unsupported/);
assert.equal(run(`(()=>{const a=JSON.parse(JSON.stringify(DATA)),b=JSON.parse(JSON.stringify(DATA));
 a.model.expressions=[{name:'Stage',kind:'m',expression:'1'}];b.model.expressions=[{name:'Stage',kind:'m',expression:'2'}];
 return compareExtracts(a,b).filter(r=>r.kind==='Shared expression').length})()`),1);
assert.equal(run(`(()=>{const a=JSON.parse(JSON.stringify(DATA)),b=JSON.parse(JSON.stringify(DATA));
 a.model.tables[0].calculationGroupDefinition={precedence:1,calculationItems:[{name:'X',expression:'1',formatStringDefinition:{expression:'"0"'}}]};
 b.model.tables[0].calculationGroupDefinition={precedence:2,calculationItems:[{name:'X',expression:'2',formatStringDefinition:{expression:'"0.0"'}}]};
 return compareExtracts(a,b).filter(r=>r.kind==='Calculation group').length})()`),1);
run("impactNode=nodeId('c','Sales','Amount');switchTab('impact');switchTab('cleanup');inspectNode(nodeId('m','Sales','Total'));switchTab('impact')");
assert.equal(node('impact-field').value,run('impactNode'));
assert.match(node('main').innerHTML,/<p>measure · Sales\[Total\]<\/p>/);
run("impactQuery='no match';switchTab('impact')");assert.equal(run('impactNode'),'');
// Actual HTML handler, entity-decoded as in a browser, must not execute names.
run(`M.tables[0].name="x');globalThis.reviewMarker=1;//";drawLineage()`);
const handler=[...node('lineage-board').innerHTML.matchAll(/onclick="([^"]*)"/g)].map(m=>m[1]).find(s=>s.includes('reviewMarker'));
run(decode(handler));assert.equal(run('globalThis.reviewMarker'),undefined);
