// UX rework, step 1: characterization of the behaviour the rework must keep (real Chromium).
// Run through check_browser.py: node browser_ux_contract.cjs <pbidocgen-browser.html>
// Each assertion pins something a reader or another surface depends on today: hash routes and their
// aliases (rels / rel-list stay addressable after the Relationships consolidation), Back, the page
// scope changing rows and CSV export, opening a relationship from the list, and the mobile menu.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const step=name=>console.log('  ok '+name);
(async()=>{
 const browser=await chromium.launch(process.env.CHROMIUM_PATH?{headless:true,executablePath:process.env.CHROMIUM_PATH}:{headless:true});
 const errors=[];
 const open=async(viewport)=>{
  const context=await browser.newContext({viewport});
  const page=await context.newPage();
  page.on('pageerror',error=>errors.push(error.message));
  page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  return page;
 };
 const url=pathToFileURL(path.resolve(process.argv[2])).href;
 const active=page=>page.evaluate(()=>activeTab);
 try{
  const page=await open({width:1440,height:900});
  // ---- deep links: every available tab opens from its hash, unknown hashes fall back to the overview
  await page.goto(url);
  assert.equal(await active(page),'overview');
  assert.equal(new URL(page.url()).hash,'#overview','the opening view is written to the address');
  const tabs=await page.evaluate(()=>TABS.filter(t=>t.avail).map(t=>t.id));
  for(const id of ['overview','rels','rel-list','columns','measures','tables','pages']) assert.ok(tabs.includes(id),id+' is available in the fixture');
  for(const id of tabs){
   await page.goto(url+'#'+id);
   assert.equal(await active(page),id,'#'+id+' opens '+id);
  }
  step('every available view opens from its own address ('+tabs.length+' views)');
  for(const [alias,id] of [['layout','pages'],['src-queries','power-query']]){
   if(!tabs.includes(id)) continue;
   await page.goto(url+'#'+alias);
   assert.equal(await active(page),id,'#'+alias+' still opens '+id);
  }
  await page.goto(url+'#no-such-view');
  assert.equal(await active(page),'overview');
  step('older aliases still resolve and an unknown address falls back to the overview');

  // ---- Back walks the reader's own steps between views
  await page.goto(url+'#overview');
  await page.evaluate(()=>switchTab('rels'));
  await page.evaluate(()=>switchTab('rel-list'));
  assert.equal(new URL(page.url()).hash,'#rel-list');
  await page.goBack();
  await page.waitForFunction(()=>activeTab==='rels');
  await page.goBack();
  await page.waitForFunction(()=>activeTab==='overview');
  step('Back returns through rel-list, rels and the overview');

  // ---- the relationship list opens a relationship on the diagram
  await page.goto(url+'#rel-list');
  const listed=await page.locator('#main button[onclick^="showRelationship("]').count();
  assert.ok(listed>0,'the list offers relationships to show');
  await page.locator('#main button[onclick^="showRelationship("]').first().click();
  await page.waitForFunction(()=>activeTab==='rels');
  assert.ok(await page.evaluate(()=>relSel.rel>=0),'the chosen relationship is selected on the diagram');
  step('choosing a relationship in the list opens it on the diagram');

  // ---- page scope: the selection changes the rows shown and the CSV export
  await page.goto(url+'#columns');
  const pages=await page.evaluate(()=>(R?.pages||[]).map(p=>p.id));
  assert.ok(pages.length>0,'the fixture has report pages');
  await page.evaluate(()=>{window.__csv=[];window.exportCsvFile=(csv,name)=>window.__csv.push({csv,name});});
  const rowsFor=async scope=>{
   await page.selectOption('#global-page',scope);
   await page.waitForFunction(s=>pageScope===s,scope);
   await page.evaluate(()=>downloadColumnCsv());
   return page.evaluate(()=>window.__csv.at(-1).csv.trim().split('\r\n').length-1);
  };
  const all=await rowsFor('*');
  let narrower=null;
  for(const id of pages){ const n=await rowsFor(id); if(n<all){ narrower=id; break; } }
  assert.ok(narrower,'one page narrows the column export ('+all+' rows for all pages)');
  assert.equal(await page.evaluate(()=>window.__csv.at(-1).name),'column-page-usage.csv');
  assert.equal(await page.inputValue('#global-page'),narrower,'the select shows the chosen page');
  await page.evaluate(()=>switchTab('measures'));
  assert.equal(await page.evaluate(()=>pageScope),narrower,'the chosen page carries to the next view');
  step('the report page selection narrows the column rows and their CSV export, and carries between views');

  // ---- object links: #o/<id> opens the object, and an unknown object explains itself
  const object=await page.evaluate(()=>OBJECTS.find(o=>o.kind==='measure')?.id);
  assert.ok(object,'the fixture has a measure object');
  await page.goto(url+'#o/'+encodeURIComponent(object));
  assert.equal(await active(page),'measures');
  await page.goto(url+'#o/'+encodeURIComponent('no-such-object'));
  assert.equal(await active(page),'overview');
  assert.match(await page.locator('#finder-note').innerText(),/does not contain/);
  step('object links open their object; a missing one says so on the overview');
  await page.context().close();

  // ---- the mobile menu: a toggle that reports its state, closes on choosing a view
  const phone=await open({width:390,height:844});
  await phone.goto(url);
  const toggle=phone.locator('#nav-toggle');
  assert.ok(await toggle.isVisible(),'the menu button shows at 390 px');
  assert.equal(await toggle.getAttribute('aria-expanded'),'false');
  assert.equal(await toggle.getAttribute('aria-controls'),'nav-sections');
  await toggle.click();
  assert.equal(await toggle.getAttribute('aria-expanded'),'true');
  await phone.evaluate(()=>switchTab('measures'));
  assert.equal(await toggle.getAttribute('aria-expanded'),'false','choosing a view closes the menu');
  const width=await phone.evaluate(()=>document.documentElement.scrollWidth);
  assert.ok(width<=390,'no sideways scroll at 390 px: '+width);
  step('at 390 px the menu button opens and closes the sections and the page does not scroll sideways');
  await phone.context().close();

  assert.deepEqual(errors,[],'no page errors');
  step('no page errors');
 }finally{ await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
