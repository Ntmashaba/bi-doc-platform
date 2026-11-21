// UX rework, step 1: characterization of the behaviour the rework must keep (real Chromium).
// Run through check_browser.py: node browser_ux_contract.cjs <pbidocgen-browser.html>
// Each assertion pins something a reader or another surface depends on today: hash routes and their
// aliases (rels / rel-list stay addressable after the Relationships consolidation), Back, the page
// scope changing rows and CSV export, opening a relationship from the list, and the mobile menu.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const step=name=>console.log('  ok '+name);
const {checkMobileNav}=require('../../../packages/engines/tests/ux_mobile_nav.cjs');
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

  // ---- step 2 chrome: no standalone breadcrumb or catalogue link, the page selection only where it acts
  await page.goto(url+'#overview');
  assert.equal(await page.locator('.breadcrumb').count(),0,'no breadcrumb');
  assert.equal(await page.locator('a[href*="pbi-home"], .library-back').count(),0,'no link to a catalogue that was not written');
  // Measured, not listed: a view offers the selection exactly when its content changes with it.
  const measured=await page.evaluate(()=>{
   const res={},scopes=['*','',...R.pages.map(p=>p.id)];
   for(const t of TABS.filter(t=>t.avail&&t.id!=='pages'&&t.id!=='report-details')){
    const seen=new Set();let select=false;
    for(const s of scopes){
     pageScope=s;switchTab(t.id,false,true);
     select=select||!!document.getElementById('global-page');
     const c=document.getElementById('main').cloneNode(true);
     c.querySelector('.scope-bar')?.remove();c.querySelector('.subtabs')?.remove();
     seen.add(c.innerHTML);
    }
    res[t.id]={changes:seen.size>1,select};
   }
   pageScope='*';return res;
  });
  for(const [id,{changes,select}] of Object.entries(measured)){
   if(changes) assert.ok(select,id+' changes with the page and offers the selection');
   else if(select) assert.ok(await page.evaluate(id=>PAGE_SCOPED_VIEWS.has(id),id),id+' offers the selection without using it');
  }
  for(const id of ['overview','rels','rel-list','security','warnings','cleanup','bookmarks','power-query']) if(measured[id]) assert.equal(measured[id].select,false,id+' has no page selection');
  step('the report page selection shows only on views whose content follows it');
  // The choice is kept where it is not shown, and a sibling count that follows it says so.
  await page.goto(url+'#visuals');
  await page.selectOption('#global-page',pages[0]);
  await page.evaluate(()=>switchTab('bookmarks'));
  assert.equal(await page.locator('#global-page').count(),0);
  assert.equal(await page.evaluate(()=>pageScope),pages[0],'the selection is retained');
  assert.match(await page.locator('.scope-bar').innerText(),/Visuals, Filters and Field manifest counts follow the page/);
  await page.locator('.scope-bar button',{hasText:'Show all pages'}).click();
  assert.equal(await page.evaluate(()=>pageScope),'*');
  assert.equal(await page.locator('.scope-bar').count(),0,'with all pages, Bookmarks has no bar at all');
  await page.evaluate(()=>switchTab('visuals'));
  assert.equal(await page.inputValue('#global-page'),'*');
  step('a hidden selection is retained, named where sibling counts follow it, and can be cleared there');
  // Coverage: no success badge anywhere; analysis gaps stay reachable where the analysis is used.
  assert.equal(await page.getByText('Full analysis coverage').count(),0);
  const gaps=await page.evaluate(()=>(DATA.columns?.issues||[]).length);
  for(const id of ['overview','columns','cleanup']){
   await page.evaluate(id=>switchTab(id),id);
   assert.equal(await page.locator('.scope-bar .coverage').count(),gaps?1:0,id+(gaps?' reports the analysis gaps':' shows no coverage badge'));
  }
  await page.evaluate(()=>switchTab('rels'));
  assert.equal(await page.locator('.coverage').count(),0,'the relationship diagram does not depend on the column analysis');
  // With a gap recorded, the warning is on the analysis views and opens to the gap itself.
  await page.evaluate(()=>{DATA.columns.issues=['Unresolved report binding Sales[Ghost]'];switchTab('columns');});
  await page.locator('.scope-bar .coverage summary').click();
  assert.match(await page.locator('.coverage-pop').innerText(),/Unresolved report binding Sales\[Ghost\]/);
  await page.evaluate(()=>switchTab('overview'));
  assert.equal(await page.locator('.scope-bar .coverage').count(),1,'the overview reports it too');
  await page.evaluate(()=>switchTab('security'));
  assert.equal(await page.locator('.coverage').count(),0);
  await page.evaluate(()=>{DATA.columns.issues=[];});
  step('no repeated coverage badge; analysis gaps stay on the views that use the analysis');
  await page.context().close();

  // ---- the mobile menu: a toggle that reports its state, closes on choosing a view
  const phone=await open({width:390,height:844});
  await phone.goto(url);
  const toggle=phone.locator('#nav-toggle');
  assert.ok(await toggle.isVisible(),'the menu button shows at 390 px');
  assert.equal(await toggle.getAttribute('aria-expanded'),'false');
  assert.equal(await toggle.getAttribute('aria-controls'),'nav-sections');
  const [title,button]=[await phone.locator('nav .doc-title').boundingBox(),await toggle.boundingBox()];
  assert.ok(button.y<title.y+title.height&&title.y<button.y+button.height,'the menu button sits beside the title: '+JSON.stringify({title,button}));
  await toggle.click();
  assert.equal(await toggle.getAttribute('aria-expanded'),'true');
  await phone.evaluate(()=>switchTab('measures'));
  assert.equal(await toggle.getAttribute('aria-expanded'),'false','choosing a view closes the menu');
  const width=await phone.evaluate(()=>document.documentElement.scrollWidth);
  assert.ok(width<=390,'no sideways scroll at 390 px: '+width);
  step('at 390 px the menu button opens and closes the sections and the page does not scroll sideways');
  await phone.context().close();

  const widths=await checkMobileNav(browser,url,{toggle:'#nav-toggle',label:'#nav-current',sectionButtons:'.nav-btn',section:page=>page.evaluate(()=>document.querySelector('#nav-sections [aria-current]').childNodes[0].textContent.trim())});
  step('the narrow-screen menu works by keyboard, Escape and focus at '+widths.join(' and ')+' px, and stays out of the way on a wide screen');
  assert.deepEqual(errors,[],'no page errors');
  step('no page errors');
 }finally{ await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
