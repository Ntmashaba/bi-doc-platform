// The seven sections, the Overview counts and the Data Sources controls in real Chromium (UI rework, Change 6).
// Run through check_browser.py: node browser_sections.cjs <pbidocgen-browser.html>
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const {checkOverviewCounts,OPENS}=require('./overview_counts.cjs');
const step=name=>console.log('  ok '+name);
const SECTIONS=[['overview','Overview',/^Overview\. A summary of this file/],
 ['sources','Data Sources',/^Data Sources\. Data source settings in Power BI Desktop/],
 ['power-query','Power Query',/^Power Query\. The Power Query Editor in Power BI Desktop/],
 ['table','Table view',/^Table view\. Table view in Power BI Desktop/],
 ['model','Model view',/^Model view\. Model view in Power BI Desktop/],
 ['dax','DAX query view',/^DAX query view\. DAX query view in Power BI Desktop/],
 ['report','Report view',/^Report view\. Report view in Power BI Desktop/]];
// Every view the document had before the restructure, and where it is now: section, or a document action.
const MIGRATION={overview:'overview',warnings:'overview',cleanup:'overview',
 sources:'sources','primary-sources':'sources','table-sources':'sources','source-objects':'sources',
 'power-query':'power-query',tables:'table',columns:'table',
 rels:'model',security:'model',lineage:'model',impact:'model',matrix:'model',usage:'model',
 measures:'dax','calc-columns':'dax','calc-tables':'dax','calc-groups':'dax',
 pages:'report',layout:'report',filters:'report',manifest:'report',bookmarks:'report',
 compare:'utility','report-details':'utility'};
(async()=>{
 const browser=await chromium.launch(process.env.CHROMIUM_PATH?{headless:true,executablePath:process.env.CHROMIUM_PATH}:{headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:900}});
 const page=await context.newPage();const errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
 const url=pathToFileURL(path.resolve(process.argv[2])).href;
 try{
  await page.goto(url);
  // ---- seven sections, in order, in the sidebar; each opens with one line naming the Power BI view
  assert.deepEqual(await page.locator('nav .nav-btn').allInnerTexts(),SECTIONS.map(s=>s[1]));
  for(const [id,label,line] of SECTIONS){
   await page.locator('#sec-'+id).click();
   assert.equal(await page.locator('#sec-'+id).getAttribute('aria-current'),'true',label+' is marked in the sidebar');
   assert.equal(await page.locator('nav .nav-btn[aria-current]').count(),1);
   assert.match(await page.locator('#view-line').innerText(),line);
   assert.equal(await page.locator('#main > *').nth(1).getAttribute('id'),'view-line',label+' opens with the line, right under the breadcrumb');
   // every tab of the section opens with the same line
   for(const tab of await page.evaluate(id=>sectionTabs(SECTIONS.find(s=>s.id===id)).filter(t=>t.avail).map(t=>t.id),id)){
    await page.evaluate(t=>switchTab(t),tab);
    assert.match(await page.locator('#view-line').innerText(),line,tab);
    assert.equal(await page.locator('#sec-'+id).getAttribute('aria-current'),'true',tab+' keeps its section marked');
   }
  }
  step('seven sections in order, mirrored in the sidebar, each tab opening with the line that names the Power BI view');
  // ---- the feature migration map: every earlier view is reachable, from the sidebar and by its old link
  const where=await page.evaluate(()=>Object.fromEntries(TABS.map(t=>[t.id,sectionOf(t.id).id])));
  assert.deepEqual(where,MIGRATION,'every view sits where the migration map says');
  assert.ok(await page.evaluate(()=>TABS.every(t=>t.avail)),'the fixture has every view available');
  for(const [tab,section] of Object.entries(MIGRATION)){
   await page.evaluate(()=>switchTab('overview'));
   if(section==='utility') await page.locator('#util-'+tab).click();
   else{ await page.locator('#sec-'+section).click(); if(await page.locator('#nav-'+tab).count()) await page.locator('#nav-'+tab).click(); }
   assert.equal(await page.evaluate(()=>activeTab),tab,tab+' is reachable by clicking');
   assert.ok((await page.locator('#main h1').first().innerText()).length>0);
   await page.goto(url+'#'+tab);
   assert.equal(await page.evaluate(()=>activeTab),tab,'#'+tab+' still opens it');
  }
  for(const [old,now] of [['src-objects','source-objects'],['src-queries','power-query']]){
   await page.goto(url+'#'+old);await page.evaluate(()=>routeReport());
   assert.equal(await page.evaluate(()=>activeTab),now,'the old link #'+old+' opens '+now);
  }
  // Compare extracts and documentation metadata are actions: below the sections, and on the Overview
  assert.deepEqual(await page.locator('nav .nav-util').allInnerTexts(),['Compare extracts','Documentation details']);
  await page.locator('#util-report-details').click();
  assert.equal(await page.locator('#view-line').count(),0,'an action is not one of the views');
  assert.equal(await page.locator('nav .nav-btn[aria-current]').count(),0);
  assert.equal(await page.locator('#util-report-details').getAttribute('aria-current'),'true');
  assert.match(await page.locator('#main h1').innerText(),/^Documentation details$/);
  await page.evaluate(()=>switchTab('overview'));
  await page.locator('#action-compare').click();
  assert.equal(await page.evaluate(()=>activeTab),'compare');
  step('every earlier view is reachable; Compare extracts and Documentation details are document actions');
  // ---- Overview: each number is a link, and equals the number of items in the list it opens
  await page.evaluate(()=>switchTab('overview'));
  const shown=await page.locator('.stat-link').evaluateAll(els=>els.map(el=>el.id.replace('count-','')));
  assert.deepEqual(shown,Object.keys(OPENS),'the Overview shows these counts, in this order');
  assert.match(await page.locator('.stat-link').first().innerText(),/^\d+\s*Source tables$/,'"Source tables" is the headline');
  // a selected report page must not change what the numbers count
  await page.evaluate(()=>setPageScope('p2'));
  const ids=await checkOverviewCounts(page,{before:()=>page.evaluate(()=>{pageScope='p2';})});
  assert.deepEqual(ids,shown);
  step('each Overview number is a link and equals the number of items in the list it opens');
  // ---- automatic date tables: in no headline count, collapsed at the end of Table view
  await page.evaluate(()=>{pageScope='*';switchTab('overview');});
  const overview=await page.locator('#main').innerText();
  assert.match(overview,/1 automatic date table that Power BI adds for date columns is in none of these numbers/);
  const total=await page.evaluate(()=>M.tables.length);
  const counted=['source-tables','calc-tables','calc-groups','other-tables'];
  let sum=0;for(const id of counted) sum+=Number(await page.locator('#count-'+id+' .big').innerText());
  assert.equal(sum,total-1,'the table counts add up to every table but the automatic date table');
  await page.locator('#main button.xl',{hasText:'Table view'}).first().click();
  await page.waitForFunction(()=>activeTab==='tables'&&document.getElementById('tbl-group-auto')?.open);
  const groups=await page.locator('#tbl-list .tbl-group').evaluateAll(els=>els.map(el=>[el.dataset.group,el.tagName]));
  assert.deepEqual(groups,[['source','SECTION'],['calculated','SECTION'],['groups','SECTION'],['other','SECTION'],['auto','DETAILS']],'automatic date tables come last');
  await page.evaluate(()=>switchTab('tables',true,true));
  assert.equal(await page.locator('#tbl-group-auto').evaluate(el=>el.open),false,'and are closed until asked for');
  assert.deepEqual(await page.locator('#tbl-group-auto details[data-table]').evaluateAll(els=>els.map(el=>el.dataset.table)),['LocalDateTable_1f']);
  assert.ok(await page.locator('#tbl-group-auto details[data-table]').first().isHidden());
  assert.equal(Number(await page.locator('#nav-tables .n').innerText()),total-1,'the Tables tab count leaves them out');
  // the same table, found by the finder, opens the closed group
  await page.locator('#finder-input').fill('LocalDateTable_1f');
  await page.locator('#finder-list [role=option][aria-label^="LocalDateTable_1f, automatic date table"]').click();
  await page.waitForFunction(()=>document.getElementById('tbl-group-auto').open);
  assert.ok(await page.locator('#tbl-group-auto details[data-table]').first().isVisible());
  // their columns and relationships are apart too, and can be shown
  await page.evaluate(()=>switchTab('columns',true,true));
  assert.match(await page.locator('#column-count').innerText(),/· 1 column of automatic date tables not shown$/);
  const before=Number(/· (\d+) distinct columns/.exec(await page.locator('#column-count').innerText())[1]);
  await page.locator('#column-auto-date').check();
  assert.equal(Number(/· (\d+) distinct columns/.exec(await page.locator('#column-count').innerText())[1]),before+1);
  step('automatic date tables are in no headline count and sit closed at the end of Table view');
  // ---- Data Sources: the query control says what the file holds, in the three agreed labels
  await page.evaluate(()=>{pageScope='*';switchTab('table-sources',true,true);});
  const control=async table=>{const row=page.locator('#table-source-rows tr',{has:page.locator('td:nth-child(3) b',{hasText:new RegExp('^'+table+'$')})}).first();
   return {row,text:(await row.locator('td').last().innerText()).replace(/\s+/g,' ')};};
  let c=await control('Native');
  assert.match(c.text,/Native SQL from the file Full M script/);
  await c.row.locator('summary',{hasText:'Native SQL from the file'}).click();
  assert.equal(await c.row.locator('pre').innerText(),'SELECT Value FROM dbo.Facts WHERE Year = 2024');
  c=await control('Budget');
  assert.match(c.text,/Native SQL from the file/);
  assert.ok(!/Full M script/.test(c.text),'a SQL partition has no M script to link to');
  c=await control('Sales');
  assert.match(c.text,/Source expression \(M\) Full M script/);
  await c.row.locator('summary',{hasText:'Source expression (M)'}).click();
  assert.equal(await c.row.locator('pre').innerText(),'Sql.Database("server", "db")');
  assert.match(await c.row.locator('.query-control .body p').innerText(),/^Step S: Connects to SQL Server: server server, database db$/);
  c=await control('Dynamic');
  assert.match(c.text,/Native query unavailable The statement is built when the query runs, so its text is not in the file\. Full M script/);
  assert.equal(await c.row.locator('.query-control details').count(),0,'nothing to open when the text is not in the file');
  await c.row.locator('button.full-m').click();
  await page.waitForFunction(()=>activeTab==='power-query'&&document.getElementById('pq-title')?.textContent==='Dynamic');
  await page.goBack();
  assert.equal(await page.evaluate(()=>activeTab),'table-sources');
  // the old wording is gone everywhere
  for(const tab of await page.evaluate(()=>TABS.filter(t=>t.avail).map(t=>t.id))){
   await page.evaluate(t=>{pageScope='*';switchTab(t);document.querySelectorAll('#main details').forEach(d=>d.open=true);},tab);
   const text=await page.locator('#main').innerText();
   assert.ok(!/Show SQL query|Show Power Query \(M\) query|View source code/.test(text),'old query wording in '+tab);
  }
  step('query control: Native SQL from the file / Source expression (M) / Native query unavailable, each with Full M script');
  // ---- the same control in a source's details, with the authentication type only when the file gives it
  await page.evaluate(()=>{pageScope='*';switchTab('sources',true,true);});
  const openSource=async name=>{await page.locator('#source-list-rows button.xl',{hasText:name}).first().click();
   await page.locator('#inspector').waitFor();return (await page.locator('#inspector').innerText()).replace(/\s+/g,' ');};
  let text=await openSource('dbo.Budget');
  assert.match(text,/Authentication type Windows integrated security/);
  assert.match(text,/Budget Native SQL from the file/);
  await page.evaluate(()=>closeInspector());
  text=await openSource('dbo.Orders');
  assert.match(text,/Authentication type Not available from this file/);
  assert.match(text,/Power BI keeps credentials outside the file/);
  assert.match(text,/Sales Source expression \(M\) Full M script/);
  await page.locator('#inspector button.full-m').first().click();
  await page.waitForFunction(()=>activeTab==='power-query'&&document.getElementById('pq-title')?.textContent==='Sales');
  await page.evaluate(()=>{switchTab('source-objects',true,true);});
  const objects=(await page.locator('#source-object-rows').innerText()).replace(/\s+/g,' ');
  assert.match(objects,/Native SQL from the file/);assert.match(objects,/Source expression \(M\)/);assert.match(objects,/Native query unavailable/);
  step('a source shows its authentication type only when the file supplies it, else "Not available from this file"');
  // ---- versions beside the generation time
  await page.evaluate(()=>switchTab('overview'));
  assert.match(await page.locator('#generated-line').innerText(),/^Generated \d{4}-\d\d-\d\d \d\d:\d\d UTC · bidoc not recorded · pbi-doc-gen \d+\.\d+\.\d+ · mode: combined$/);
  await page.evaluate(()=>{DATA.producer={engine:'pbi-doc-gen',engineVersion:'9.9.9',bidoc:'1.2.3'};switchTab('overview');});
  assert.match(await page.locator('#generated-line').innerText(),/· bidoc 1\.2\.3 · pbi-doc-gen 9\.9\.9 ·/);
  await page.evaluate(()=>{delete DATA.producer;switchTab('overview');});
  assert.match(await page.locator('#generated-line').innerText(),/· bidoc not recorded · pbi-doc-gen not recorded ·/);
  step('bidoc and engine versions sit beside the generation time, "not recorded" when the document does not say');
  // ---- narrow screens
  await page.setViewportSize({width:390,height:844});
  await page.goto(url);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'the Overview fits a phone');
  await page.locator('#nav-toggle').click();
  assert.deepEqual(await page.locator('nav .nav-btn:visible').allInnerTexts(),SECTIONS.map(s=>s[1]));
  assert.equal(await page.locator('nav .nav-util:visible').count(),2);
  await page.locator('#sec-dax').click();
  assert.equal((await page.locator('#nav-current').innerText()).trim(),'DAX query view');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  for(const tab of ['table-sources','bookmarks','sources']){
   await page.evaluate(t=>switchTab(t),tab);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),tab+' fits a phone');
  }
  step('the sections and actions work at phone width');
  assert.deepEqual(errors,[]);
  console.log('Section, Overview count and Data Sources checks passed in Chromium.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
