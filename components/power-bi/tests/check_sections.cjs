// The seven sections, the feature migration map, the Overview counts and the Data Sources wording, in the
// generated script with a minimal DOM adapter (UI rework, Change 6). Real-browser checks: browser_sections.cjs.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[2],'utf8');
const nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{id,value:'',innerHTML:'',textContent:'',hidden:false,style:{},
  classList:{add(){},remove(){},contains:()=>false},setAttribute(k,v){this['@'+k]=v;},removeAttribute(){},hasAttribute:()=>false,
  getAttribute(){return null;},focus(){},scrollIntoView(){},getBoundingClientRect:()=>({height:10,top:0}),tagName:'DIV',dataset:{}});return nodes.get(id);};
const browserWindow={scrollTo(){},location:{hash:''},addEventListener(){},history:{pushState(){},replaceState(){}}};
const context=vm.createContext({console,setTimeout:()=>0,clearTimeout(){},document:{getElementById:node,querySelectorAll:()=>[],
  createElement:()=>({}),body:{appendChild(){}}},window:browserWindow});
vm.runInContext(html.match(/<script>([\s\S]*)<\/script>/)[1],context);
const run=s=>vm.runInContext(s,context);
const same=(a,b)=>assert.equal(JSON.stringify(a),JSON.stringify(b));
const text=s=>s.replace(/<[^>]+>/g,' ').replace(/&quot;/g,'"').replace(/&amp;/g,'&').replace(/&#39;/g,"'").replace(/\s+/g,' ').trim();
const show=tab=>{run(`switchTab(${JSON.stringify(tab)})`);return node('main').innerHTML;};

// Seven sections, in this order, named after the Power BI views.
same(run('SECTIONS.map(s=>s.label)'),['Overview','Data Sources','Power Query','Table view','Model view','DAX query view','Report view']);
// The sidebar mirrors them; Compare extracts and Documentation details are actions, not sections.
const nav=node('nav').innerHTML;
same([...nav.matchAll(/id="sec-([a-z-]+)"[^>]*>([^<]+)</g)].map(m=>m[2]),run('SECTIONS.map(s=>s.label)'));
same([...nav.matchAll(/id="util-([a-z-]+)"/g)].map(m=>m[1]),['compare','report-details']);
same(run('UTILITIES.map(u=>u.label)'),['Compare extracts','Documentation details']);
assert.ok(!run("SECTIONS.some(s=>s.tabs.includes('compare')||s.tabs.includes('report-details'))"));

// Feature migration map: every view the document had before the rework, and the section it is in now.
const MIGRATION={overview:'overview',warnings:'overview',cleanup:'overview',
  sources:'sources','primary-sources':'sources','table-sources':'sources','source-objects':'sources',
  'power-query':'power-query',tables:'table',columns:'table',
  rels:'model','rel-list':'model',security:'model',lineage:'model',impact:'model',matrix:'model',usage:'model',
  measures:'dax','calc-columns':'dax','calc-tables':'dax','calc-groups':'dax',
  pages:'report',visuals:'report',filters:'report',manifest:'report',bookmarks:'report',
  compare:'utility','report-details':'utility'};
for(const [tab,section] of Object.entries(MIGRATION)){
  assert.ok(run(`TABS.some(t=>t.id===${JSON.stringify(tab)})`),tab+' is still a view');
  assert.equal(run(`sectionOf(${JSON.stringify(tab)}).id`),section,tab+' is in '+section);
}
// ...and nothing exists outside the map.
same(run('TABS.map(t=>t.id).sort()'),Object.keys(MIGRATION).sort());
// Addresses saved before the rework still open the view that took the content over.
for(const [old,now] of [['src-objects','source-objects'],['src-queries','power-query'],['layout','pages']]){
  assert.equal(run(`TAB_ALIASES[${JSON.stringify(old)}]`),now,old+' is an alias of '+now);
  if(!run(`TABS.find(t=>t.id===${JSON.stringify(now)}).avail`)) continue;   // this fixture has no report
  run(`switchTab(${JSON.stringify(old)})`);assert.equal(run('activeTab'),now,old+' opens '+now);
}

// Every view of a section opens with the one line that names the Power BI view; an action has none.
for(const tab of run('TABS.filter(t=>t.avail).map(t=>t.id)')){
  const main=show(tab),section=run(`sectionOf(${JSON.stringify(tab)})`);
  assert.equal(run('activeTab'),tab);
  const line=/<p class="view-line" id="view-line">([\s\S]*?)<\/p>/.exec(main);
  if(section.utility){assert.equal(line,null,tab+' is an action and names no Power BI view');continue;}
  assert.ok(line,tab+' opens with the view line');
  assert.equal((main.match(/id="view-line"/g)||[]).length,1);
  assert.ok(text(line[1]).startsWith(section.label+'. '),tab+': '+text(line[1]));
  assert.ok(main.indexOf('id="view-line"')<main.indexOf('<h1'),tab+': the line comes before the heading');
}
assert.match(text(show('tables')),/^.*Table view\. Table view in Power BI Desktop/);
assert.match(text(show('overview')),/Power BI Desktop has no view like it/);
// A section with nothing to show stays in the sidebar, disabled, and says why (this fixture has no report).
assert.match(nav,/<button class="nav-btn disabled" id="sec-report" aria-disabled="true" title="No report was supplied, so there are no pages or visuals to show\."/);

// Overview: the numbers are links, "Source tables" leads, automatic date tables are in none of them.
const overview=show('overview');
const counts=[...overview.matchAll(/<button type="button" class="card stat stat-link" id="count-([a-z-]+)" onclick="openCount\('[a-z-]+'\)"[^>]*><span class="big">([\d,]+)<\/span><span class="lbl">([^<]+)<\/span>/g)].map(m=>[m[1],Number(m[2]),m[3]]);
same(counts,[['source-tables',3,'Source tables'],['calc-tables',1,'Calculated tables'],['columns',6,'Columns'],['calc-columns',1,'Calculated columns'],
  ['measures',1,'Measures'],['relationships',1,'Relationships'],['queries',2,'Power Query queries'],['sources',3,'Data sources'],
  ['warnings',(show('warnings').match(/<tr/g)||[]).length-1,'Warnings']]);
// (the coverage ratios under "Documentation and quality" are shares of the visible objects, and say so)
const contains=overview.slice(overview.indexOf('id="contents"'),overview.indexOf('<h2',overview.indexOf('id="contents"')+1));
assert.equal((contains.match(/class="big"/g)||[]).length,counts.length,'no number under "What this file contains" is plain text');
assert.equal(run('M.tables.length'),5);assert.equal(run('M.relationships.length'),2);
assert.match(text(overview),/1 automatic date table that Power BI adds for date columns is in none of these numbers; it is listed at the end of Table view/);
// each number is the size of the group it opens
same(run("['source','calculated','groups','other'].map(id=>tablesInGroup(TABLE_GROUPS.find(g=>g.id===id)).length)"),[3,1,0,0]);
const tables=show('tables');
const group=id=>{const start=tables.indexOf(`id="tbl-group-${id}"`);if(start<0)return null;
  const next=tables.indexOf('id="tbl-group-',start+10);return tables.slice(start,next<0?undefined:next);};
const cards=id=>[...(group(id)||'').matchAll(/data-table="([^"]+)"/g)].map(m=>m[1]);
same(cards('source'),['Budget','Orders','Sales']);same(cards('calculated'),['Calendar']);same(cards('auto'),['LocalDateTable_1']);
assert.match(tables,/<details class="[^"]*tbl-auto[^"]*" id="tbl-group-auto"(?! open)/,'automatic date tables are closed');
assert.ok(tables.indexOf('id="tbl-group-auto"')>tables.indexOf('id="tbl-group-calculated"'),'and come last');
assert.equal(run("TABS.find(t=>t.id==='tables').count()"),4);
assert.equal(run("TABS.find(t=>t.id==='columns').count()"),6);
show('columns');run('filterColumns()');
assert.match(node('column-count').textContent,/· 6 distinct columns · 1 column of automatic date tables not shown$/,'the Columns list leaves automatic date table columns out');
run("openCount('source-tables')");assert.equal(run('activeTab'),'tables');
run("pageScope='x';openCount('measures')");assert.equal(run('pageScope'),'*','a number counts the whole file, whatever page was selected');
// the relationship to an automatic date table is listed apart, and is in no count
const rels=show('rel-list');run('filterRelList()');
assert.equal((node('rel-list-rows').innerHTML.match(/<tr>/g)||[]).length,1);assert.equal(node('rel-list-count').textContent,'1 relationship');
assert.match(text(rels),/Relationships to automatic date tables 1 added by Power BI for date columns; in no headline count .* Sales \[Amount\] many → one LocalDateTable_1 \[Date\]/);
assert.equal(run("TABS.find(t=>t.id==='rels').count()"),1);
assert.equal(run('countedRelationships().length'),1);

// Versions beside the generation time: what the payload records, "not recorded" for what it does not.
const generated=()=>text(/<p class="sub" id="generated-line">([\s\S]*?)<\/p>/.exec(show('overview'))[1]);
assert.match(generated(),/^Generated \d{4}-\d\d-\d\d \d\d:\d\d UTC · bidoc not recorded · pbi-doc-gen \d+\.\d+\.\d+ · mode: semantic-only$/);
run("DATA.producer={engine:'pbi-doc-gen',engineVersion:'9.9.9',bidoc:'1.2.3'}");
assert.match(generated(),/· bidoc 1\.2\.3 · pbi-doc-gen 9\.9\.9 · mode/);
run("DATA.producer={engine:'pbi-doc-gen',engineVersion:'<b>x</b>',bidoc:'  '}");
assert.ok(show('overview').includes('pbi-doc-gen &lt;b&gt;x&lt;/b&gt;'),'a version is text, never markup');
assert.match(generated(),/· bidoc not recorded ·/);
run('delete DATA.producer');   // a document generated before versions were recorded
assert.match(generated(),/· bidoc not recorded · pbi-doc-gen not recorded · mode/);

// Data Sources query control: three labels, each with the link to the whole script. "Show SQL query" is gone.
const q=run('PQ.queries.find(q=>q.table==="Sales")');
const control=o=>run(`queryControl(${o})`);
const withSql=control('{sql:"SELECT 1",query:PQ.queries.find(q=>q.table==="Sales")}');
assert.match(withSql,/<summary>Native SQL from the file<\/summary>[\s\S]*<pre class="code">SELECT 1<\/pre>[\s\S]*class="xl full-m"[^>]*>Full M script<\/button>/);
const expression=control('{query:PQ.queries.find(q=>q.table==="Sales")}');
assert.match(expression,/<summary>Source expression \(M\)<\/summary>/);assert.match(expression,/>Full M script</);
assert.match(text(expression),/Step S : Connects to SQL Server|Step S: Connects to SQL Server/);
assert.ok(!text(expression).includes('Table.SelectRows'),'the source expression is the step that names the source, not the whole script');
const unavailable=control('{unavailable:true,query:PQ.queries.find(q=>q.table==="Sales"),note:"why"}');
assert.match(text(unavailable),/^Native query unavailable why Full M script$/);
assert.ok(!unavailable.includes('<pre'));
// a shared document without query code says so, and never shows the marker as if it were code
assert.match(text(control('{sql:"[query code withheld]"}')),/^Query code withheld Not part of this shared document\.$/);
assert.equal(control('{}'),'');
const everyView=run('TABS.filter(t=>t.avail).map(t=>t.id)').map(show).join('\n');
assert.ok(!everyView.includes('Show SQL query'),'"Show SQL query" is not used any more');
// a SQL partition shows its statement; an M partition its source expression
const bySource=show('table-sources');run('filterTableSources()');
const rows=node('table-source-rows').innerHTML;
assert.match(rows,/Native SQL from the file<\/summary><div class="body"><pre class="code">SELECT \* FROM dbo\.Budget<\/pre>/);
assert.match(rows,/Source expression \(M\)/);

// Authentication type: only what the file supplies, and only for the tables that read through that data source.
const auth=run('sourceGroups().map(g=>[g.tables.join(),sourceAuthentication(g)])');
same(auth.sort(),[['Budget','Windows integrated security'],['Orders','User name and password'],['Sales','Not available from this file']]);
assert.equal(run('NO_AUTHENTICATION'),'Not available from this file');
// A shared document without query code says so where a table's source query would be, in the Impact inspector too.
run("DATA.tableSources.filter(r=>r.table==='Budget').forEach(r=>{r.query='[query code withheld]';})");
const impact=run("impactDetails(nodeId('c','Budget','Amount'))");
assert.match(text(impact),/Query code withheld Not part of this shared document\./);assert.ok(!impact.includes('<pre class="code">[query code withheld]'));
console.log('Section, migration map, Overview count and Data Sources checks passed.');
