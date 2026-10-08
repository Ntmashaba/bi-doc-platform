/* Run after acceptance.py. PLAYWRIGHT_CHROMIUM_EXECUTABLE may select a local binary. */
const {chromium}=require('playwright');
const fs=require('fs'),path=require('path');
const {pathToFileURL}=require('url');
const {checkOverviewCounts}=require('../components/power-bi/tests/overview_counts.cjs');
(async()=>{
 const out=path.resolve(__dirname,'../samples/output');
 const results=JSON.parse(fs.readFileSync(path.join(out,'results.json'),'utf8'));
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 let errors=[];page.on('pageerror',e=>errors.push(e.message));
 const checks=[];
 const expect=(ok,message)=>{if(!ok)throw Error(message);};
 // The search the reporting user ran (UI rework spec, Change 1): the Tables box finds a table by one of its
 // columns, names the column, restores on clear and says when nothing matches; the finder lands on the column.
 async function adventureWorksSearch(page){
  await page.evaluate(()=>{pageScope='*';switchTab('tables',false,true);});
  const search=page.locator('#table-search'),cards=page.locator('#tbl-list details[data-table]:visible');
  expect(await cards.count()===10,'AdventureWorks Sales lists 10 tables, found '+await cards.count());
  await search.pressSequentially('Mth of year');
  expect(await cards.count()===1,'"Mth of year" should leave one table, found '+await cards.count());
  expect(/^Date\b/.test(await cards.first().locator('summary').innerText()),'"Mth of year" should return the Date table');
  expect(await cards.first().locator('.tbl-match').innerText()==='Matching column: Mth of year','the matching column is named');
  await search.fill('');
  expect(await cards.count()===10,'clearing the box restores the full list');
  await search.fill('no such table or column');
  expect(await page.locator('#table-search-status').innerText()==='No matching tables or columns.','an unmatched search says so');
  expect(await cards.count()===0,'an unmatched search shows no tables');
  await search.fill('');
  await page.evaluate(()=>switchTab('overview'));
  const finder=page.locator('#finder-input');
  await finder.click();await finder.pressSequentially('Mth of y');
  const option=page.locator('#finder-list [role=option]').first();
  expect(await option.getAttribute('aria-label')==='Mth of year, calculated column, Date','the finder shows name, kind and parent: '+await option.getAttribute('aria-label'));
  await page.keyboard.press('Enter');
  const row=await page.evaluate(()=>'#'+columnAnchor('Date','Mth of year'));
  await page.waitForFunction(s=>document.querySelector(s)?.classList.contains('obj-hit'),row);
  expect(await page.evaluate(()=>activeTab)==='tables','the column opens in Tables');
  expect(await page.locator(row).evaluate(el=>el===document.activeElement),'the column row has keyboard focus');
  await finder.fill('');
  console.log('AdventureWorks Sales: Tables search and finder checks passed');
 }
 for(const r of results){
  errors=[];await page.goto(pathToFileURL(path.join(out,r.file)).href);
  await page.waitForFunction(()=>document.body.innerText.length>250);
  const pbi=await page.evaluate(()=>typeof DATA!=='undefined'&&'liveSource' in DATA);
  if(pbi){
   const tabs=await page.evaluate(()=>TABS.filter(t=>t.avail).map(t=>t.id));
   for(const tab of tabs)await page.evaluate(t=>switchTab(t),tab);
   await page.evaluate(()=>switchTab('overview'));
   if(['abf','pbix'].some(ext=>r.sample.endsWith('.'+ext))&&!r.sample.startsWith('live-connection')){
    if(!(await page.locator('body').innerText()).includes('Extraction coverage'))throw Error('Missing extraction coverage: '+r.file);
   }
   // Every object in the search index opens its view and is shown there, in every sample.
   const lost=await page.evaluate(()=>OBJECTS.filter(o=>{
    if(!goObject(o.id,{history:false}))return true;
    const el=document.getElementById(homeOf(o).el);
    return !el||!el.getClientRects().length;
   }).map(o=>o.kind+' '+o.name));
   if(lost.length)throw Error(r.file+': '+lost.length+' indexed objects cannot be shown, e.g. '+lost.slice(0,5).join('; '));
   // Power Query: every query opens; its script is on the page exactly when the publication status says so.
   if(tabs.includes('power-query')){
    const wrong=await page.evaluate(()=>{switchTab('power-query');return PQ.queries.filter(q=>{
     selectQuery(q.objectId,false);
     const d=document.getElementById('pq-detail'),withheld=q.publication==='withheld';
     if(d.querySelector('#pq-title').textContent!==q.name)return true;
     if(withheld)return !!d.querySelector('pre')||!!d.querySelector('.pq-step-name')||d.innerHTML.includes('[query code withheld]');
     return !!pqCode(q).trim()!==!!d.querySelector('pre')||(q.steps.status==='parsed')!==!!d.querySelector('.pq-step-name');
    }).map(q=>q.name);});
    if(wrong.length)throw Error(r.file+': Power Query entries shown wrongly: '+wrong.slice(0,5).join('; '));
    const publication=await page.evaluate(()=>[...new Set(PQ.queries.map(q=>q.publication))]);
    if(r.profile==='shared'&&publication.some(p=>p!=='withheld'))throw Error(r.file+': a shared document shows query code: '+publication);
    if(r.profile==='local'&&publication.includes('withheld'))throw Error(r.file+': a local document withholds query code');
    if(r.sample==='DP500 08 Composite model.pbix'){
     const parameters=await page.evaluate(()=>PQ.queries.filter(q=>q.kind==='parameter').map(q=>q.name).sort());
     expect(parameters.join()==='Culture,SqlServerDatabase,SqlServerInstance','DP500 08 parameters: '+parameters);
    }
   }
   // Seven sections named after the Power BI views; every Overview number equals the list it opens;
   // automatic date tables are in no headline count; the versions sit beside the generation time.
   const sections=await page.locator('nav [id^="sec-"]').evaluateAll(els=>els.map(el=>el.textContent.trim()));
   expect(sections.join()==='Overview,Data Sources,Power Query,Table view,Model view,DAX query view,Report view',r.file+': sections are '+sections);
   expect(await page.locator('#util-compare').count()===1&&await page.locator('#util-report-details').count()===1,r.file+': document actions missing');
   const unreachable=await page.evaluate(()=>TABS.filter(t=>t.avail&&!sectionOf(t.id)).map(t=>t.id));
   expect(!unreachable.length,r.file+': views in no section: '+unreachable);
   const viewLines=await page.evaluate(()=>TABS.filter(t=>t.avail).filter(t=>{switchTab(t.id);const line=document.getElementById('view-line');
    return !sectionOf(t.id).utility&&!(line&&line.textContent.trim().startsWith(sectionOf(t.id).label+'.'));}).map(t=>t.id));
   expect(!viewLines.length,r.file+': views that do not open with the line naming the Power BI view: '+viewLines);
   await checkOverviewCounts(page);
   const auto=await page.evaluate(()=>has.model?M.tables.filter(isAutoDate).length:0);
   if(auto){
    const sum=await page.locator('#count-source-tables .big, #count-calc-tables .big, #count-calc-groups .big, #count-other-tables .big').evaluateAll(els=>els.reduce((n,el)=>n+Number(el.textContent.replace(/,/g,'')),0));
    expect(sum===await page.evaluate(()=>M.tables.length)-auto,r.file+': automatic date tables are in a headline count');
    await page.evaluate(()=>switchTab('tables',true,true));
    expect(await page.locator('#tbl-group-auto').evaluate(el=>!el.open&&!el.nextElementSibling),r.file+': automatic date tables are not collapsed at the end of Table view');
    expect(await page.locator('#tbl-group-auto details[data-table]').count()===auto,r.file+': automatic date table group is incomplete');
    await page.evaluate(()=>switchTab('overview'));
   }
   const generated=await page.locator('#generated-line').innerText();
   // acceptance.py calls the engine library directly, not bidoc, so the bidoc version is honestly "not recorded" here;
   // the bidoc paths (generate, batch, worker) are covered by apps/generator/tests.
   expect(/^Generated \d{4}-\d\d-\d\d \d\d:\d\d UTC · bidoc not recorded · pbi-doc-gen \d+\.\d+\.\d+ · /.test(generated),r.file+': versions missing beside the generation time: '+generated);
   expect(!(await page.locator('#main').innerText()).includes('Show SQL query'),r.file+': "Show SQL query" is still shown');
   // Table kinds: one of the seven for every table; the calculation tabs list what their counts say.
   if(await page.evaluate(()=>has.model)){
    const kinds=await page.evaluate(()=>({
     bad:M.tables.filter(t=>!DEFINED_KINDS.includes(definedBy(t).kind)).map(t=>t.name),
     auto:tablesDefinedBy('Automatic date table').map(t=>t.name),
     counts:['calc-columns','calc-tables','calc-groups','measures'].map(id=>TABS.find(t=>t.id===id).count()),
     lists:[calcColumns().length,tablesDefinedBy('Calculated table').length,tablesDefinedBy('Calculation group').length,M.measures.length]}));
    if(kinds.bad.length)throw Error(r.file+': tables without a defined-by kind: '+kinds.bad.slice(0,5).join('; '));
    if(kinds.counts.join()!==kinds.lists.join())throw Error(r.file+': a calculation tab count differs from its list: '+kinds.counts+' vs '+kinds.lists);
    if(r.sample==='DP500 08 Composite model.pbix'){
     expect(kinds.auto.length>0&&kinds.auto.every(n=>/^(LocalDateTable|DateTableTemplate)_/.test(n)),'DP500 08 automatic date tables: '+kinds.auto);
     expect(kinds.auto.some(n=>n.startsWith('DateTableTemplate_')),'DP500 08 has the date table template');
    }
    if(r.sample==='AdventureWorks Sales.pbix'){
     expect(await page.evaluate(()=>isCalcColumn('Date','Mth of year')),'"Mth of year" is a calculated column');
     await page.evaluate(()=>{pageScope='*';switchTab('calc-columns');});
     expect(await page.locator('#calc-column-list details.calc-col .mea-name',{hasText:'Mth of year'}).count()===1,'"Mth of year" is listed under Calculated Columns');
    }
   }
   if(r.sample==='AdventureWorks Sales.pbix'&&r.profile==='local')await adventureWorksSearch(page);
   await page.evaluate(()=>switchTab('overview'));
  }
  if(!pbi){
   const tabs=await page.evaluate(()=>TABS.filter(t=>t.avail()).map(t=>t.id));
   for(const tab of tabs)await page.evaluate(t=>switchTab(t),tab);
  }
  if(errors.length)throw Error(r.file+': '+errors.join('; '));
  if(r.profile==='local'&&(r.sample.includes('Tabular.abf')||r.sample.includes('11 Dual')||r.synthetic)){
   await page.screenshot({path:path.join(out,r.sample.replace(/[^a-z0-9]/gi,'_')+'.png'),fullPage:true});
  }
  checks.push({file:r.file,passed:true});console.log('Browser passed',r.file);
 }
 fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify(checks,null,2));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
