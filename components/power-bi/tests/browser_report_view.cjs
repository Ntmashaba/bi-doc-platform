// Report view in real Chromium (UI rework, Change 4: Report view).
// Run through check_browser.py: node browser_report_view.cjs <pbidocgen-browser.html>
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const {pagesProblems}=require('./view_geometry.cjs');
const step=name=>console.log('  ok '+name);
(async()=>{
 const browser=await chromium.launch(process.env.CHROMIUM_PATH?{headless:true,executablePath:process.env.CHROMIUM_PATH}:{headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:900}});
 const page=await context.newPage();const errors=[],requests=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
 page.on('request',r=>{if(!/^(file|data|blob):/.test(r.url()))requests.push(r.url());});
 const url=pathToFileURL(path.resolve(process.argv[2])).href;
 const words=async locator=>(await locator.innerText()).replace(/\s+/g,' ').trim();
 const frame=id=>page.locator(id).boundingBox();
 const box=(p,v)=>page.locator('#'+'vbox-'+[...p].map(c=>c.codePointAt(0).toString(16)).join('_')+'-u'+[...v].map(c=>c.codePointAt(0).toString(16)).join('_'));
 try{
  await page.goto(url);
  await page.locator('#sec-report').click();
  assert.equal(await page.evaluate(()=>activeTab),'pages');
  assert.deepEqual((await page.locator('.subtabs .subtab').allInnerTexts()).map(t=>t.replace(/\s*\d+$/,'')),['Pages','Visuals','Filters','Field manifest','Bookmarks']);
  // ---- the page list: every page in report order, with what sets it apart
  const tabs=page.locator('#page-strip .page-tab');
  assert.deepEqual((await tabs.allInnerTexts()).map(t=>t.replace(/\s+/g,' ').trim()),['1 Sales / Same / page [p1]','2 Sales / Same / page [p2]',
   '3 Order details drillthrough page','4 Sales tooltip tooltip page · hidden','5 Imported page page type not recorded']);
  assert.equal(await page.locator('#page-strip [aria-current="page"]').getAttribute('data-page'),'p1','the first page in report order opens first');
  assert.equal(await page.locator('#page-strip').evaluate(el=>el.tagName+'/'+el.getAttribute('role')),'DIV/navigation');
  assert.equal(await page.locator('.page-view').count(),1,'one page at a time');
  assert.equal(await page.locator('#global-page').count(),0,'the page list is the page selector here');
  assert.deepEqual(await pagesProblems(page),[]);
  await page.evaluate(()=>{showReportPage('p1');pickVisual('p1','v1');pickVisual('p1','v1');});
  step('the page list names every page in report order and says which are drillthrough, tooltip, hidden or not recorded');
  // ---- one page: the layout on top with the panel beside it, then every visual with its fields
  await tabs.nth(2).click();
  assert.equal(await page.locator('#page-strip [aria-current="page"]').getAttribute('data-page'),'p3');
  assert.equal(await page.evaluate(()=>document.activeElement.dataset.page),'p3','focus stays on the page list');
  assert.match(await words(page.locator('.page-head')),/^Order details drillthrough page$/);
  assert.match(await words(page.locator('.page-facts')),/^Drillthrough page · 1,280 × 720 · 2 data visuals · 1 decorative$/);
  assert.equal(await words(page.locator('#page-type-note')),'A drillthrough page: readers reach it by drilling through from another page on Dim[ID].');
  const layout=await frame('#page-viewport'),side=await frame('#page-panel'),surface=await frame('#page-surface'),list=await frame('#page-visuals');
  assert.ok(side.x>=layout.x+layout.width-1&&side.y<layout.y+layout.height,'the panel is beside the layout');
  assert.ok(list.y>=surface.y+surface.height-1,'the list of visuals is below the layout');
  assert.equal(await page.locator('#page-surface .visual-box').count(),3,'every placed visual is a box');
  assert.deepEqual(await page.locator('#page-visuals tbody tr').evaluateAll(rs=>rs.map(r=>r.cells[0].querySelector('button').textContent)),['Order total','Table · ID']);
  assert.match(await words(page.locator('#page-decorative summary')),/^1 decorative visual without data Shape$/);
  assert.match(await words(page.locator('.page-feeds')),/Dim .*ID/);
  // nothing selected: the Filters pane for the page
  let panel=await words(page.locator('#page-panel'));
  assert.match(panel,/^Select a visual in the layout or the list for its fields and the filters on it\. Filters /);
  assert.match(panel,/Filters on this visual Select a visual to see its own\. One visual has filters on this page: Table · ID 1 filter /);
  assert.match(panel,/Filters on this page 1 Positive amounts Sales\[Amount\] Advanced: > 0 hidden from readers /);
  assert.match(panel,/Filters on all pages 1 Calendar\[Year\] fx Basic: 2024$/);
  assert.doesNotMatch(panel.split('Filters on this page')[1].split('Filters on all pages')[0],/Dim\[ID\]/,'the drillthrough field is not in the Filters pane');
  step('a page: layout on top, panel beside it, the Filters pane grouped on this visual / this page / all pages, every visual below');
  // ---- select a visual: its box, its row and the panel; the rest of the page is not redrawn
  await page.evaluate(()=>{document.getElementById('page-strip').dataset.kept='1';});
  await box('p3','d2').click();
  assert.equal(await box('p3','d2').getAttribute('aria-pressed'),'true');
  assert.equal(await box('p3','d1').getAttribute('aria-pressed'),'false');
  assert.equal(await page.locator('#page-visuals tr.is-selected').count(),1);
  assert.equal(await page.evaluate(()=>document.getElementById('page-strip').dataset.kept),'1','selecting a visual does not redraw the page');
  panel=await words(page.locator('#page-panel'));
  assert.match(panel,/^Table · ID Clear selection Table \(tableEx · d2\) Fields Dim\[ID\] Values In the model Dim\[ID\] Filters /);
  assert.match(panel,/Filters on this visual 1 Dim\[ID\] TopN: 10 Filters on this page 1 Positive amounts/);
  assert.match(panel,/Filters on all pages 1 Calendar\[Year\] fx Basic: 2024$/);
  await box('p3','d2').click();
  assert.equal(await box('p3','d2').getAttribute('aria-pressed'),'false','the same visual again clears it');
  assert.match(await words(page.locator('#page-panel')),/^Select a visual/);
  // keyboard: a box is a button; Escape clears and leaves focus on the box
  await box('p3','d1').focus();await page.keyboard.press('Enter');
  assert.equal(await box('p3','d1').getAttribute('aria-pressed'),'true');
  assert.match(await words(page.locator('#page-panel')),/^Order total Clear selection Card \(card · d1\) Fields Sales\[Total\] \(m\) Values In the model Sales\[Total\]/);
  await page.keyboard.press('Escape');
  assert.equal(await box('p3','d1').getAttribute('aria-pressed'),'false');
  assert.equal(await page.evaluate(()=>document.activeElement.dataset.visualId),'d1');
  // a click on the empty part of the layout clears too
  await box('p3','d1').click();
  const vp=await frame('#page-viewport');
  await page.locator('#page-viewport').click({position:{x:Math.round(vp.width*.92),y:Math.round(vp.height*.7)}});
  assert.equal(await page.locator('#page-surface [aria-pressed="true"]').count(),0);
  // from the list below: the visual is selected and the layout with its panel comes into view
  await page.locator('#page-visuals').scrollIntoViewIfNeeded();await page.evaluate(()=>window.scrollBy(0,400));
  await page.locator('#page-visuals tbody tr').nth(1).getByRole('button',{name:'Table · ID'}).click();
  assert.equal(await box('p3','d2').getAttribute('aria-pressed'),'true');
  const shown=await box('p3','d2').boundingBox();
  assert.ok(shown.y>=0&&shown.y<900,'the selected box is on screen');
  assert.ok((await frame('#page-panel')).y<900,'and so is the panel');
  await page.locator('#visual-clear').click();
  step('selecting a visual presses its box, marks its row and fills the panel; Escape, the box again or the canvas clears it');
  // ---- the tooltip page and the page whose type is not recorded
  await tabs.nth(3).click();
  assert.match(await words(page.locator('.page-head')),/^Sales tooltip tooltip page hidden$/);
  assert.equal(await words(page.locator('#page-type-note')),'A tooltip page: Power BI shows it as the tooltip of visuals that use its tooltip fields: Sales[Amount]. Hidden: readers of the published report do not see it in the page list.');
  assert.ok(await box('p4','t1').evaluate(el=>el.classList.contains('hidden-visual')&&getComputedStyle(el).borderStyle.includes('dashed')),'a hidden visual is dashed');
  await tabs.nth(4).click();
  assert.match(await words(page.locator('.page-head')),/^Imported page page type not recorded$/);
  assert.match(await words(page.locator('.page-facts')),/^Page type not recorded · /);
  assert.equal(await words(page.locator('#page-type-note')),'Page type not recorded: this document does not say whether it is an ordinary page, a tooltip page or a drillthrough page.');
  assert.match(await words(page.locator('.page-no-layout')),/No visual on this page has usable coordinates/);
  await page.locator('#page-visuals tbody tr button',{hasText:'Region slicer'}).click();
  assert.match(await words(page.locator('#page-panel')),/^Region slicer Clear selection Slicer \(slicer · u1\)/);
  step('tooltip, hidden and not-recorded pages say so; a visual without coordinates is selected from the list');
  // ---- Visuals: every visual of every page, searchable; each opens on its page
  await page.locator('#nav-visuals').click();
  assert.equal(await page.locator('#visual-list-rows tr').count(),7);
  assert.equal(await words(page.locator('#visual-count')),'7 visuals');
  await page.locator('#visual-search').fill('order details');
  assert.equal(await page.locator('#visual-list-rows tr').count(),3);assert.equal(await words(page.locator('#visual-count')),'3 of 7 visuals');
  await page.locator('#visual-search').fill('');
  assert.match(await words(page.locator('#visual-list-rows tr').nth(5)),/^Sales tooltip tooltip page hidden Card · Total hidden Card Sales\[Total\] \(m\) —$/);
  await page.locator('#visual-list-rows tr').nth(3).getByRole('button',{name:'Table · ID'}).click();
  await page.waitForFunction(()=>activeTab==='pages');
  assert.equal(await page.locator('#page-strip [aria-current="page"]').getAttribute('data-page'),'p3');
  assert.equal(await box('p3','d2').getAttribute('aria-pressed'),'true');
  assert.ok(await box('p3','d2').evaluate(el=>el===document.activeElement),'the visual has keyboard focus');
  await page.goBack();await page.waitForFunction(()=>activeTab==='visuals');
  step('Visuals lists every visual of every page, is searchable, and opens a visual selected on its page');
  // ---- Filters: the flat list, with its scope, searchable
  await page.locator('#nav-filters').click();
  const filterRows=await page.evaluate(()=>filterList().length);
  assert.equal(await page.locator('#filter-list-rows tr').count(),filterRows);
  assert.equal(filterRows,await page.evaluate(()=>allFilters().length)-4,'the filter on all pages is listed once, not once per page');
  assert.equal(await page.locator('#filter-list-rows tr',{hasText:'Every page'}).count(),1);
  assert.deepEqual([...new Set(await page.locator('#filter-list-rows tr td:first-child .badge').allInnerTexts())].sort(),['All pages','Page','Visual']);
  await page.locator('#filter-search').fill('drillthrough');
  assert.equal(await page.locator('#filter-list-rows tr').count(),1);
  assert.match(await words(page.locator('#filter-list-rows tr')),/^Page drillthrough field .*Order details .*Dim\[ID\]/);
  await page.locator('#filter-search').fill('tooltip field');
  assert.equal(await page.locator('#filter-list-rows tr').count(),1,"a tooltip page's field is a tooltip field");
  assert.match(await words(page.locator('#filter-list-rows tr')),/^Page tooltip field .*Sales tooltip .*Sales\[Amount\]/);
  await page.locator('#filter-search').fill('positive');
  assert.match(await words(page.locator('#filter-list-rows tr')),/Positive amounts Sales\[Amount\] hidden from readers Advanced > 0$/);
  step('Filters keeps the flat list with its scope column, for searching');
  // ---- Bookmarks: every bookmark, the page it opens, a link to that page
  await page.locator('#nav-bookmarks').click();
  assert.deepEqual(await page.locator('#bookmark-list tbody tr').evaluateAll(rs=>rs.map(r=>r.innerText.replace(/\s+/g,' ').trim())),
   ['Saved Sales / Same / page [p2] Sales[BookmarkOnly]','Tooltip state Group: Saved views Sales tooltip No field references',
    'Gone A page that is not in this report No field references','Unplaced Not recorded No field references']);
  await page.locator('#bookmark-list').getByRole('button',{name:'Sales tooltip'}).click();
  await page.waitForFunction(()=>activeTab==='pages');
  assert.equal(await page.locator('#page-strip [aria-current="page"]').getAttribute('data-page'),'p4');
  step('Bookmarks lists every bookmark with the page it opens');
  // ---- object links and the Report page selection
  await page.goto(url+'#o/'+encodeURIComponent('pbi:page:p3'));
  await page.waitForFunction(()=>activeTab==='pages'&&shownPage().id==='p3');
  await page.goto(url+'#layout');await page.evaluate(()=>routeReport());
  assert.equal(await page.evaluate(()=>activeTab),'pages','an old Page layout link opens Pages');
  await page.evaluate(()=>{switchTab('columns');setPageScope('p4');switchTab('pages');});
  assert.equal(await page.locator('#page-strip [aria-current="page"]').getAttribute('data-page'),'p4','a page chosen for the usage views opens in Pages');
  const options=await page.evaluate(()=>{switchTab('columns');return [...document.querySelectorAll('#global-page option')].map(o=>o.textContent).join('|');});
  assert.match(options,/Order details \(drillthrough page\)\|Sales tooltip \(tooltip page, hidden\)\|Imported page \(page type not recorded\)$/);
  await page.evaluate(()=>setPageScope('*'));
  step('object links and old Page layout links open Pages on the right page; the page selector names page types');
  // ---- the page is in the address: Back and Forward move between pages, a reload opens the same page and visual
  await page.goto(url);
  await page.locator('#sec-report').click();
  await tabs.nth(2).click();await tabs.nth(3).click();
  assert.equal(await page.evaluate(()=>decodeURIComponent(location.hash)),'#o/pbi:page:p4');
  await page.goBack();await page.waitForFunction(()=>shownPage().id==='p3');
  assert.equal(await page.locator('#page-strip [aria-current="page"]').getAttribute('data-page'),'p3');
  await page.goBack();await page.waitForFunction(()=>activeTab==='pages'&&shownPage().id==='p1');
  await page.goForward();await page.waitForFunction(()=>shownPage().id==='p3');
  await page.goForward();await page.waitForFunction(()=>shownPage().id==='p4');
  await box('p4','t1').click();
  assert.equal(await page.evaluate(()=>decodeURIComponent(location.hash)),'#o/pbi:visual:p4/t1','selecting a visual names it in the address');
  await page.reload();
  await page.waitForFunction(()=>activeTab==='pages'&&shownPage().id==='p4');
  assert.equal(await box('p4','t1').getAttribute('aria-pressed'),'true','a reload opens the same visual, selected');
  await page.keyboard.press('Escape');
  await page.locator('#visual-clear').click().catch(()=>{});
  await page.evaluate(()=>clearPickedVisual());
  assert.equal(await page.evaluate(()=>decodeURIComponent(location.hash)),'#o/pbi:page:p4','clearing it leaves the page in the address');
  await page.goBack();await page.waitForFunction(()=>shownPage().id==='p3');
  step('choosing a page is a step in history; the address names the page and the selected visual');
  // ---- narrow screens and print
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(()=>{showReportPage('p3');pickVisual('p3','d2');});
  const narrowLayout=await frame('#page-viewport'),narrowPanel=await frame('#page-panel');
  assert.ok(narrowPanel.y>=narrowLayout.y+narrowLayout.height-1,'on a phone the panel is under the layout');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth),'no sideways page scroll on a phone');
  // a long page name, labelled with its type, does not widen the Report page selector past the screen
  await page.evaluate(()=>{R.pages[2].name='Satisfaction comparison by region and quarter';switchTab('columns');});
  assert.match(await page.locator('#global-page option[value="p3"]').innerText(),/\(drillthrough page\)$/);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth),'the page selector fits a phone');
  await page.setViewportSize({width:1440,height:900});
  await page.evaluate(()=>showReportPage('p3'));
  await page.emulateMedia({media:'print'});
  assert.equal(await page.locator('#page-strip .page-tab:visible').count(),1,'printing shows the page being printed, not the whole list');
  assert.ok(await page.locator('#page-visuals').isVisible());
  await page.emulateMedia({media:'screen'});
  step('a phone stacks the panel under the layout; print keeps the page and its list of visuals');
  assert.deepEqual(requests,[],'nothing is fetched');
  assert.deepEqual(errors,[]);
  console.log('Report view checks passed in Chromium.');
 }catch(error){
  console.error(error);if(errors.length)console.error('page errors:',errors);
  await page.screenshot({path:path.join(require('node:os').tmpdir(),'browser-report-view-failure.png'),fullPage:true}).catch(()=>{});
  process.exitCode=1;
 }finally{await browser.close();}
})();
