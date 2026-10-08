// The Model view relationship surface in the actual generated script, with a minimal DOM adapter (UI rework, Change 7).
// Layout, focus and the panel beside the diagram are checked in a real browser by browser_relationships.cjs.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[2],'utf8');
const nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{id,value:'',innerHTML:'',textContent:'',hidden:false,style:{},
  classList:{add(){},remove(){},contains:()=>false},setAttribute(k,v){this['@'+k]=v;},removeAttribute(){},focus(){},scrollIntoView(){}});return nodes.get(id);};
const browserWindow={scrollTo(){},location:{hash:''},addEventListener(){},history:{pushState(){},replaceState(){}}};
const context=vm.createContext({console,setTimeout:()=>0,clearTimeout(){},document:{getElementById:node,querySelectorAll:()=>[],
  createElement:()=>({}),body:{appendChild(){}}},window:browserWindow});
vm.runInContext(html.match(/<script>([\s\S]*)<\/script>/)[1],context);
const run=s=>vm.runInContext(s,context);
const same=(a,b)=>assert.equal(JSON.stringify(a),JSON.stringify(b));
const unescape=s=>s.replace(/&quot;/g,'"').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&#39;/g,"'").replace(/&amp;/g,'&');
const text=s=>unescape(s.replace(/<[^>]+>/g,' ')).replace(/\s+/g,' ').trim();
const surface=()=>node('rel-canvas').innerHTML,panel=()=>text(node('rel-panel').innerHTML);

// Model view has the surface and, as its own tab, the full list.
same(run("SECTIONS.find(s=>s.id==='model').tabs.slice(0,2)"),['rels','rel-list']);
same(run("['rels','rel-list'].map(id=>TABS.find(t=>t.id===id).label)"),['Relationships','Relationships list']);
run("switchTab('rels')");assert.equal(run('activeTab'),'rels');
const main=node('main').innerHTML;
// One surface: the diagram and the panel are two halves of one card, and no table of relationships follows it.
assert.match(main,/<div class="card rel-surface" id="rel-surface"[^>]*>\s*<div class="rel-main">[\s\S]*<aside id="rel-panel" class="rel-panel"/);
assert.ok(!main.includes('<table'),'the detail is in the panel, not in a table under the diagram');

// ---- what is drawn
const boxes=()=>[...surface().matchAll(/<g class="rel-box([^"]*)" id="rel-box-(\d+)" data-table="([^"]*)"([^>]*)>[\s\S]*?<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"/g)]
  .map(m=>({cls:m[1].trim().split(/\s+/),index:+m[2],name:unescape(m[3]),attrs:m[4],x:+m[5],y:+m[6],w:+m[7],h:+m[8]}));
const edges=()=>[...surface().matchAll(/<g class="rel-edge ([^"]*)" id="rel-edge-(\d+)" data-rel="\d+"([^>]*)>([\s\S]*?)<\/g>/g)].map(m=>{
  const d=/<path class="rel-line" d="M([\d.-]+),([\d.-]+) C[^"]* ([\d.-]+),([\d.-]+)"([^>]*)>/.exec(m[4]);
  const arrow=/<path class="rel-arrow" d="([^"]+)" transform="translate\(([\d.-]+),([\d.-]+)\) rotate\(([\d.-]+)\)"/.exec(m[4]);
  return {cls:m[1].split(/\s+/),index:+m[2],attrs:m[3],x1:+d[1],y1:+d[2],x2:+d[3],y2:+d[4],line:d[5],arrow,open:/<circle class="rel-arrow rel-open"/.test(m[4]),
    cards:[...m[4].matchAll(/<text class="rel-card"[^>]*>([^<]*)</g)].map(c=>c[1]),title:unescape(/<title>([^<]*)</.exec(m[4])[1])};});
const R=run('countedRelationships()');
same(R.map(r=>r.fromTable+'>'+r.toTable),['Sales>Customer','Sales>Product','Product>Category','Sales>Date','Sales>Date','Returns>Product','Customer>Geography','Returns>Sales']);
const B=boxes(),E=edges(),at=name=>B.find(b=>b.name===name);
// every table but the automatic date table is a box, including the one with no relationships
same(B.map(b=>b.name).sort(),['Category','Customer','Date','Geography','Notes & <more>','Product','Returns','Sales']);
assert.ok(!surface().includes('LocalDateTable_1'),'automatic date tables are not drawn');
assert.equal(E.length,8,'one line per relationship');
assert.ok(at('Notes & <more>').cls.includes('no-rel'));assert.match(surface(),/<text class="rel-caption"[^>]*>No relationships<\/text>/);
for(const b of B){assert.match(b.attrs,/tabindex="0" role="button" aria-pressed="false"/);assert.ok(b.w>=160&&b.h>=48,b.name+' is readable');}
// no two boxes overlap
for(const a of B)for(const b of B)if(a!==b)assert.ok(a.x+a.w<=b.x||b.x+b.w<=a.x||a.y+a.h<=b.y||b.y+b.h<=a.y,a.name+' overlaps '+b.name);
// a box says its role; the facts stand in the middle, what hangs off them on either side
const role=name=>run(`M.tables.find(t=>t.name===${JSON.stringify(name)}).tableType`);
assert.ok(at('Sales').cls.includes('role-'+({fact:'fact',dimension:'dim','date dimension':'date'}[role('Sales')]||'other')));
assert.equal(at('Sales').x,at('Returns').x,'the two fact tables share the middle lane');
assert.ok(Math.abs(at('Category').x-at('Sales').x)>Math.abs(at('Product').x-at('Sales').x),'a snowflaked table sits one lane further out');
assert.equal(Math.sign(at('Category').x-at('Sales').x),Math.sign(at('Product').x-at('Sales').x),'on the same side as the table it hangs from');
assert.equal(Math.sign(at('Geography').x-at('Sales').x),Math.sign(at('Customer').x-at('Sales').x));
// every line starts and ends on the edge of its two tables
for(const e of E){
  const r=R[e.index],from=at(r.fromTable),to=at(r.toTable),on=(b,x,y)=>(x===b.x||x===b.x+b.w)&&y>b.y&&y<b.y+b.h;
  assert.ok(on(from,e.x1,e.y1),`${r.fromTable} → ${r.toTable} leaves its table`);assert.ok(on(to,e.x2,e.y2),`${r.fromTable} → ${r.toTable} reaches its table`);
  // cardinality at each end, in the order from, to
  same(e.cards,[r.fromCardinality==='many'?'*':'1',r.toCardinality==='many'?'*':'1']);
  assert.match(e.title,new RegExp(`^${r.fromTable}\\[${r.fromColumn}\\].* → ${r.toTable}\\[${r.toColumn}\\] · ${r.fromCardinality} to ${r.toCardinality} · ${r.isActive?'active':'inactive'} · `));
}
// inactive: dashed. Nothing else is.
same(E.filter(e=>e.cls.includes('inactive')).map(e=>e.index),[4]);
for(const e of E)assert.equal(/stroke-dasharray="7 5"/.test(e.line),e.index===4,'only the inactive relationship is dashed');
// direction: one arrowhead for single, two for both, a hollow dot when the file leaves it to Power BI
const heads=e=>e.arrow?(e.arrow[1].match(/Z/g)||[]).length:0;
same(E.map(e=>e.cls.find(c=>c.startsWith('filter-'))),['filter-single','filter-single','filter-single','filter-single','filter-single','filter-both','filter-automatic','filter-single']);
same(E.map(heads),[1,1,1,1,1,2,0,1]);
same(E.map(e=>e.open),[false,false,false,false,false,false,true,false]);
// a single arrowhead points the way filters flow: from the "to" table towards the "from" table
for(const e of E.filter(e=>heads(e)===1)){
  const r=R[e.index],from=at(r.fromTable),to=at(r.toTable);if(from.x===to.x)continue;
  const angle=+e.arrow[4]*Math.PI/180,towardsFrom=(from.x+from.w/2)-(+e.arrow[2]);
  assert.ok(Math.cos(angle)*towardsFrom>0,`${r.toTable} filters ${r.fromTable}: the arrow points at ${r.fromTable}`);
}
assert.match(E[0].title,/Single direction: Customer filters Sales$/);assert.match(E[5].title,/Filters both ways$/);assert.match(E[6].title,/Automatic: Power BI decides the direction$/);
assert.match(E[4].title,/^Sales\[ShipDate\] \(fx\) → Date\[Date\]/,'a calculated column keeps its marker');
// lines are drawn before boxes
assert.ok(surface().lastIndexOf('<g class="rel-edge')<surface().indexOf('<g class="rel-box'));
// nothing is faded and the panel describes the model until something is selected
assert.ok(!/is-faded|is-hot|opacity=/.test(surface()));
assert.match(panel(),/^This model 8 tables · 8 relationships · 1 inactive · 1 filtering both ways Select a table to see its relationships\./);
assert.match(panel(),/1 table with no relationships is at the foot of the diagram\./);
assert.match(panel(),/1 relationship to an automatic date table is not drawn; it is in the Relationships list ?\./);

// ---- selecting a table: its relationships stay, the rest fades, the detail opens in the panel
run("selectRelTable('Sales')");
{const B=boxes(),E=edges(),state=b=>b.cls.find(c=>c.startsWith('is-'))||'';
 same(B.filter(b=>state(b)==='is-selected').map(b=>b.name),['Sales']);
 assert.match(B.find(b=>b.name==='Sales').attrs,/aria-pressed="true"/);
 same(B.filter(b=>state(b)==='is-related').map(b=>b.name).sort(),['Customer','Date','Product','Returns']);
 same(B.filter(b=>state(b)==='is-faded').map(b=>b.name).sort(),['Category','Geography','Notes & <more>']);
 for(const b of B)assert.equal(/opacity="0\.3"/.test(b.attrs),state(b)==='is-faded',b.name);
 same(E.filter(e=>e.cls.includes('is-hot')).map(e=>e.index),[0,1,3,4,7]);
 same(E.filter(e=>e.cls.includes('is-faded')).map(e=>e.index),[2,5,6]);
 for(const e of E){assert.equal(/opacity="0\.22"/.test(e.attrs),e.cls.includes('is-faded'));assert.equal(/stroke-width="3"/.test(e.line),e.cls.includes('is-hot'),'the kept lines are drawn heavier');}
 // a faded line keeps what tells it apart
 assert.match(E[5].arrow[1],/Z M/);assert.ok(E[4].cls.includes('inactive')&&/stroke-dasharray/.test(E[4].line));
 assert.equal(node('rel-focus').value,'Sales');
 const p=panel();
 assert.match(p,/^Sales Clear selection /);assert.match(p,/defined by Power Query/);
 assert.match(p,/5 relationships · 1 inactive Customer /);
 assert.match(p,/Customer Sales\[CustomerKey\] → Customer\[CustomerKey\] many to one · active · Single direction: Customer filters Sales/);
 assert.match(p,/Date inactive Sales\[ShipDate\] fx → Date\[Date\] many to one · inactive · /);
 assert.match(p,/Returns Returns\[SalesKey\] → Sales\[SalesKey\] many to one · active · Single direction: Sales filters Returns/);
 assert.ok(!p.includes('Category')&&!p.includes('Geography'),'only its own relationships are listed');
 assert.match(p,/Also 1 relationship to an automatic date table, not drawn here; see the Relationships list ?\./);
 assert.match(node('rel-panel').innerHTML,/onclick="selectRelTable\(&quot;Customer&quot;\)"/,'the other table can be selected from the panel');
 assert.match(node('rel-panel').innerHTML,/onclick="goRef\(&quot;table&quot;,&quot;Sales&quot;\)|onclick="go[A-Za-z]*\([^)]*Sales/,'and the table opens in Table view');}
// the same table again clears the selection
run("selectRelTable('Sales')");assert.ok(!/is-faded|is-hot|aria-pressed="true"/.test(surface()));assert.match(panel(),/^This model/);
// a table with no relationships can be selected too, and says so
run(`selectRelTable(${JSON.stringify('Notes & <more>')})`);
assert.match(panel(),/^Notes & <more> Clear selection .* No relationships This table is related to no other table in the model\./);
assert.equal(edges().filter(e=>e.cls.includes('is-faded')).length,8);
run('clearRelSelection()');assert.match(panel(),/^This model/);assert.equal(node('rel-focus').value,'');

// ---- selecting a line: that relationship alone
run('selectRelationship(5)');
{const B=boxes(),E=edges();
 same(E.filter(e=>e.cls.includes('is-hot')).map(e=>e.index),[5]);
 same(B.filter(b=>b.cls.includes('is-related')).map(b=>b.name).sort(),['Product','Returns']);
 assert.equal(B.filter(b=>b.cls.includes('is-selected')).length,0);
 assert.match(panel(),/^Relationship Clear selection Returns → Product both ways Returns\[ProductKey\] → Product\[ProductKey\] many to one · active · Filters both ways From Returns many side To Product one side Status Active Cross-filter Filters both ways/);}
run('selectRelationship(4)');assert.match(panel(),/Status Inactive: used only when a measure asks for it \(USERELATIONSHIP\)/);
run('selectRelationship(4)');assert.match(panel(),/^This model/);

// ---- the list is its own tab: every relationship, one row each, and a way back to the diagram
run("switchTab('rel-list')");run('filterRelList()');
assert.match(node('main').innerHTML,/<h1>Relationships list<\/h1>/);assert.ok(!node('main').innerHTML.includes('rel-surface'));
const rows=()=>node('rel-list-rows').innerHTML.split('</tr>').filter(r=>r.includes('<tr>')).map(text);
assert.equal(rows().length,8);assert.equal(node('rel-list-count').textContent,'8 relationships');
assert.match(rows()[4],/^Sales \[ShipDate\] fx many → one Date \[Date\] inactive single Show$/);
assert.match(rows()[5],/^Returns \[ProductKey\] many → one Product \[ProductKey\] active both Show$/);
assert.match(rows()[6],/ active automatic Show$/);
node('rel-list-search').value='inactive';run('filterRelList()');assert.equal(rows().length,1);assert.equal(node('rel-list-count').textContent,'1 of 8 relationships');
node('rel-list-search').value='geography';run('filterRelList()');same(rows().map(r=>r.split(' ')[0]),['Customer']);
node('rel-list-search').value='zzz';run('filterRelList()');assert.match(node('rel-list-rows').innerHTML,/class="rel-none"[\s\S]*No relationships match this search\./);
node('rel-list-search').value='';run('filterRelList()');
// the relationship to the automatic date table is listed apart, closed, with no way to a diagram it is not on
const auto=/<details class="rel-auto" id="rel-auto">([\s\S]*?)<\/details>/.exec(node('main').innerHTML)[1];
assert.match(text(auto),/^Relationships to automatic date tables 1 added by Power BI for date columns; in no headline count .* Sales \[OrderDate\] many → one LocalDateTable_1 \[Date\] active single$/);
assert.ok(!auto.includes('showRelationship'));
run('showRelationship(6)');assert.equal(run('activeTab'),'rels');
same(edges().filter(e=>e.cls.includes('is-hot')).map(e=>e.index),[6]);assert.match(panel(),/^Relationship Clear selection Customer → Geography /);
run('clearRelSelection()');

// ---- the selection is kept when the reader comes back to the tab, and forgotten when the tab is opened afresh
run("selectRelTable('Customer');switchTab('rel-list');switchTab('rels')");assert.match(panel(),/^Customer Clear selection/);
assert.match(node('main').innerHTML,/<option value="Customer" selected>/);
run("zoomRel(0.4);switchTab('rels',true,true)");assert.match(panel(),/^This model/);assert.ok(!/is-faded|aria-pressed="true"/.test(surface()));
assert.equal(node('rel-zoom-label').textContent,'100%');
// ---- zoom is bounded, and the table picker selects like a click does
run('zoomRel(10)');assert.equal(node('rel-zoom-label').textContent,'250%');
run('zoomRel(-10)');assert.equal(node('rel-zoom-label').textContent,'30%');
run('fitRel()');assert.equal(node('rel-zoom-label').textContent,'100%');
run("selectRelTable('Product',true)");assert.match(panel(),/^Product Clear selection .* 3 relationships · 1 filtering both ways/);
run('clearRelSelection()');
// An unrecognised cross-filter value is shown as written, not as a direction.
run("M.relationships[0].crossFilteringBehavior='sideways';drawRelSurface()");
assert.ok(edges()[0].open&&edges()[0].cls.includes('filter-other'));assert.match(edges()[0].title,/Cross-filter value "sideways", shown as the file writes it$/);
// The legend names the hollow marker when a line has one; a model whose only relationships go to automatic date
// tables says so instead of "no relationships".
run("M.relationships[0].crossFilteringBehavior='oneDirection';switchTab('rels')");
assert.match(text(node('main').innerHTML),/Direction automatic or not recognised/);
run("savedRels=M.relationships;M.relationships=M.relationships.filter(autoDateRelationship);switchTab('rels')");
assert.equal(text(node('rel-canvas').innerHTML),"No relationships to draw: the model's only relationship is to automatic date tables, listed in the Relationships list.");
assert.ok(!/Direction automatic or not recognised/.test(node('main').innerHTML),'no hollow marker, no legend entry for it');
run("M.relationships=[];switchTab('rels')");assert.equal(text(node('rel-canvas').innerHTML),'No relationships in this model.');
run("M.relationships=savedRels");
console.log('Relationship surface, panel and list checks passed.');
