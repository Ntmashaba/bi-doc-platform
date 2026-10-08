// The Power Query view in the actual generated script, run with a minimal DOM adapter.
//   node check_power_query.cjs <document.html> <local|withheld>
// Layout, scrolling and focus are checked in a real browser by browser_power_query.cjs.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[2],'utf8'),copy=process.argv[3]||'local';
const nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{id,value:'',innerHTML:'',textContent:'',hidden:false,style:{},
  classList:{add(){},remove(){}},setAttribute(k,v){this['@'+k]=v;},focus(){}});return nodes.get(id);};
const browserWindow={scrollTo(){},location:{hash:''},addEventListener(){},history:{
  pushState(_s,_t,hash){browserWindow.location.hash=hash;},replaceState(_s,_t,hash){browserWindow.location.hash=hash;}}};
const context=vm.createContext({console,setTimeout:()=>0,clearTimeout(){},document:{getElementById:node,querySelectorAll:()=>[],
  createElement:()=>({}),body:{appendChild(){}}},window:browserWindow});
vm.runInContext(html.match(/<script>([\s\S]*)<\/script>/)[1],context);
const run=s=>vm.runInContext(s,context);
const same=(a,b)=>assert.equal(JSON.stringify(a),JSON.stringify(b));
const text=s=>s.replace(/<[^>]+>/g,' ').replace(/&quot;/g,'"').replace(/&amp;/g,'&').replace(/&#39;/g,"'").replace(/\s+/g,' ').trim();

// One entry per query, and each is an object the finder and links can open in this view.
assert.equal(run('PQ.queries.length'),run('DATA.sourceQueries.length'));
assert.ok(run("PQ.queries.every(q=>OBJECT_BY_ID.get(q.objectId)?.kind==='query'&&homeOf(OBJECT_BY_ID.get(q.objectId)).tab==='power-query')"));
assert.ok(run("TABS.some(t=>t.id==='power-query'&&t.avail)"));
run("switchTab('power-query')");
const page=node('main').innerHTML;
assert.match(text(page),/Power Query\. The Power Query Editor in Power BI Desktop: the queries that connect to sources/);
// Folders in the file's order, then the queries outside every folder; each folder's queries in the file's order.
same(run('pqFolders().map(f=>[f.label,f.queries.map(q=>q.name)])'),[['Parameters',['BaseUrl','Region']],['Staging',['Stage']],
  ['Other Queries',['Sales','Customers','fnClean','Orders / 2023','Orders / 2024','Orphan']]]);
assert.ok(page.indexOf('>Parameters <')<page.indexOf('>Staging <')&&page.indexOf('>Staging <')<page.indexOf('>Other Queries <'));
assert.equal((page.match(/class="pq-item/g)||[]).length,9,'every query is in the pane once');
// A query that is not loaded reads as such in the pane; functions and parameters are marked.
assert.match(page,/class="pq-item pq-unloaded" id="[^"]+" data-name="stage"/);
assert.match(page,/class="pq-item" id="[^"]+" data-name="sales"/);
assert.match(page,/data-name="fnclean"[\s\S]{0,400}?<span class="pq-kind" title="Function">fx<\/span>/);
assert.match(page,/data-name="region"[\s\S]{0,400}?<span class="pq-kind" title="Parameter">param<\/span>/);

const open=name=>{assert.equal(run(`selectQuery(PQ.queries.find(q=>q.name===${JSON.stringify(name)}).objectId)`),true);return node('pq-detail').innerHTML;};
// Header: name, what it loads or who uses it, load status, upstream queries and external sources.
let detail=open('Sales'),plain=text(detail);
assert.match(plain,/^Sales Query table query/);
assert.match(plain,/Loads table Sales/);
assert.match(plain,/Load status loaded Its result is a table in the model\./);
assert.match(plain,/Upstream queries Region , fnClean/);
assert.match(plain,/External sources SQL Server · srv \/ dw · dbo\.Orders/);
assert.match(detail,/onclick="goTable\(&quot;Sales&quot;\)"/,'the table is a link');
assert.match(detail,/onclick="goObject\(&quot;pbi:query:name:fnClean&quot;\)"/,'an upstream query is a link');
detail=open('Stage');plain=text(detail);
assert.match(plain,/^Stage Query shared query The staged file\./);
assert.match(plain,/Used by Customers , Orders/,'a query used by several tables is listed once, with its tables');
assert.match(plain,/Load status not loaded/);
assert.match(plain,/Upstream queries BaseUrl/);
assert.match(plain,/Read by queries Customers , Orders \/ 2023 , Orders \/ 2024/);
assert.match(plain,/Query folder Staging/);
plain=text(open('Orders / 2024'));
assert.match(plain,/Loads table Orders partitions 2024, 2024 copy/);
// Three statuses, each reported on its own.
const statuses=name=>{const d=text(open(name));return ['Expression extraction','Applied Steps','Publication'].map(t=>
  d.match(new RegExp(t+' (Complete|Known partial|Unavailable|Not recorded|Parsed|No top-level Applied Steps|Unsupported syntax|Included|Cleaned|Withheld)'))?.[1]);};
if(copy==='local'){
  same(statuses('Sales'),['Complete','Parsed','Included']);
  same(statuses('BaseUrl'),['Complete','No top-level Applied Steps','Included']);
  detail=open('Sales');plain=text(detail);
  assert.match(plain,/Applied Steps Parsed 4 steps\./);
  same([...detail.matchAll(/class="pq-step-name">([^<]*)</g)].map(m=>m[1]),['Source','Orders','Kept Rows','Cleaned']);
  // The full script, collapsed, as written.
  assert.match(detail,/<details class="pq-code" id="pq-code"><summary>Full M script/);
  assert.ok(!/<details class="pq-code"[^>]*open/.test(detail),'the script is collapsed until asked for');
  const written=block=>block.replace(/<[^>]+>/g,'').replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
  const script=detail.match(/<details class="pq-code"[\s\S]*?<pre class="code">([\s\S]*?)<\/pre>/)[1];
  assert.equal(written(script),run("DATA.sourceQueries.find(q=>q.queryName==='Sales').mCode"),'the script is shown exactly as written');
  // Each step: its words where the form is recognised, and its own expression, exactly as written.
  const stepHtml=[...detail.matchAll(/<li>([\s\S]*?)<\/li>/g)].map(m=>m[1]);
  same(stepHtml.map(h=>(h.match(/class="pq-step-says">([^<]*)</)||[])[1]||''),
    ['Connects to SQL Server: server srv, database dw','Navigates to dbo.Orders','Keeps rows where [Region] = Region','Invokes the function fnClean']);
  same(stepHtml.map(h=>written(h.match(/<pre class="code">([\s\S]*?)<\/pre>/)[1])),
    ['Sql.Database("srv", "dw")','Source{[Schema="dbo",Item="Orders"]}[Data]','Table.SelectRows(Orders, each [Region] = Region)','fnClean(#"Kept Rows")']);
  assert.match(plain,/Each step is described from its text\. A description says what a step is written to do, not what happened when it ran\./);
  assert.ok(stepHtml.every(h=>/^<details class="pq-step"><summary>/.test(h)&&!/<details class="pq-step" open/.test(h)),'steps open on request');
  // A step in a form that is not recognised keeps its name and the function it calls, and gets no sentence.
  const orders=open('Orders / 2023'),last=[...orders.matchAll(/<li>([\s\S]*?)<\/li>/g)].map(m=>m[1]).at(-1);
  assert.match(last,/class="pq-step-says">Keeps rows where \[Year\] = 2023</);
  const orphan=open('Orphan');
  assert.match(orphan,/class="pq-step-says">Builds a table from values written in the script</);
  detail=open('Sales');plain=text(detail);
  plain=text(open('fnClean'));
  assert.match(plain,/^fnClean Function shared query/);
  assert.match(plain,/This query is a function; these are the steps of its body\./);
  assert.match(text(open('BaseUrl')),/^BaseUrl Parameter shared query/);
}else{
  same(statuses('Sales'),['Complete','Parsed','Withheld']);
  same(statuses('BaseUrl'),['Complete','No top-level Applied Steps','Withheld']);
  detail=open('Sales');plain=text(detail);
  assert.match(plain,/Step names are part of the query code, which this shared document withholds\./);
  assert.match(plain,/Query code is withheld in this shared document\./);
  assert.ok(!detail.includes('pq-step-name')&&!detail.includes('<pre'),'no step and no script is rendered');
  assert.ok(!detail.includes('[query code withheld]'),'the marker is never shown as if it were code');
  assert.match(plain,/Loads table Sales/,'the facts read at generation are still there');
  assert.match(text(open('Stage')),/Used by Customers , Orders/);
}
// M is shown as text: markup in a script stays inert.
assert.equal(run("hlM('let A = \"<img src=x onerror=1>\" // <b>\\nin A')").includes('<img'),false);
assert.match(run("hlM('let A = 1 in A')"),/<span class="k">let<\/span> A = 1 <span class="k">in<\/span> A/);
// The export is unchanged: three columns, every query.
same(run('queryCsvFields.map(f=>f[1])'),['report','query name','query m code']);
const csv=run('inventoryCsv(DATA.sourceQueries,queryCsvFields)');
for(const name of run('DATA.sourceQueries.map(q=>q.queryName)')) assert.ok(csv.includes('"'+name+'"'),name+' is exported');
// The library can ask for a query by name.
assert.equal(run("VIEWER_VIEWS['pbi.query']({query:'Stage'})"),true);
assert.equal(run('activeTab'),'power-query');assert.equal(run('PQ_BY_ID.get(pqSelected).name'),'Stage');
assert.equal(run("VIEWER_VIEWS['pbi.query']({query:'No such query'})"),false);
// An object link to a query selects it.
assert.equal(run("goObject('pbi:query:name:Customers')"),true);
assert.equal(run('PQ_BY_ID.get(pqSelected).name'),'Customers');
assert.equal(browserWindow.location.hash,'#o/'+encodeURIComponent('pbi:query:name:Customers'));
console.log('Power Query view ('+copy+'): '+run('PQ.queries.length')+' queries, folders, statuses, steps and script checks passed.');
