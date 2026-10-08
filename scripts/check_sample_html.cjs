/* Run after acceptance.py. PLAYWRIGHT_CHROMIUM_EXECUTABLE may select a local binary. */
const {chromium}=require('playwright');
const fs=require('fs'),path=require('path');
const {pathToFileURL}=require('url');
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
  const search=page.locator('#table-search'),cards=page.locator('#tbl-list > details:visible');
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
