// Search and object navigation in real Chromium (UI rework, Change 1).
// Run through check_browser.py: node browser_navigation.cjs <pbidocgen-browser.html>
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const step=name=>console.log('  ok '+name);
(async()=>{
 const browser=await chromium.launch(process.env.CHROMIUM_PATH?{headless:true,executablePath:process.env.CHROMIUM_PATH}:{headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:900}});
 const page=await context.newPage();const errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
 const file=path.resolve(process.argv[2]),url=pathToFileURL(file).href;
 const finder=page.locator('#finder-input'),options=page.locator('#finder-list [role=option]');
 const idOf=(family,...parts)=>page.evaluate(([family,parts])=>objectFor(family,...parts).id,[family,parts]);
 // The object is where the reader is looking: its view is open, it is inside the window, highlighted and focused.
 const landed=async(selector,tab)=>{
  const target=page.locator(selector);
  await page.waitForFunction(s=>document.querySelector(s)?.classList.contains('obj-hit'),selector);
  assert.equal(await page.evaluate(()=>activeTab),tab);
  await assert.doesNotReject(target.waitFor({state:'visible',timeout:2000}),selector+' is visible');
  const box=await target.boundingBox(),view=page.viewportSize();
  assert.ok(box&&box.y>=0&&box.y<view.height&&box.x+box.width>0&&box.x<view.width,selector+' is inside the window: '+JSON.stringify(box));
  assert.ok(await target.evaluate(el=>el===document.activeElement||el.contains(document.activeElement)),selector+' has keyboard focus');
 };
 const col=(table,column)=>page.evaluate(([t,c])=>'#'+columnAnchor(t,c),[table,column]);
 try{
  await page.goto(url);
  // ---- the finder: browse, narrow on every keystroke, name / kind / parent, count, empty state
  await finder.click();
  assert.equal(await finder.getAttribute('aria-expanded'),'true');
  const groups=await page.locator('#finder-list .finder-kind').allInnerTexts();
  assert.deepEqual(groups.map(g=>g.replace(/\s*[\d,]+$/,'')),['Data sources','Queries','Tables','Calculated tables','Automatic date tables','Calculation groups','Columns','Calculated columns','Measures','Security roles','Pages','Visuals']);
  assert.match(await page.locator('#finder-count').innerText(),/^\d+ objects$/);
  assert.equal(await finder.getAttribute('aria-activedescendant'),null,'browsing leaves the choice to the arrow keys');
  step('with the box empty the finder is a browse list grouped by kind');
  const seen=[];
  for(const key of 'Amount'){
   await page.keyboard.type(key);
   seen.push(await page.evaluate(()=>searchObjects(document.getElementById('finder-input').value).total));
   assert.equal(await page.locator('#finder-count').innerText(),seen.at(-1)===1?'1 result':seen.at(-1)+' results');
  }
  for(let i=1;i<seen.length;i++) assert.ok(seen[i]<=seen[i-1],'narrows on every keystroke: '+seen);
  assert.ok(seen[0]>seen.at(-1)&&seen.at(-1)>=1,'narrows: '+seen);
  await finder.fill('doub');
  assert.deepEqual(await options.first().locator('span').allInnerTexts(),['Double fx','calculated column','Sales']);
  assert.equal(await options.first().locator('.fo-name abbr.fx').count(),1,'a calculated column carries the fx marker in the finder');
  assert.equal(await options.first().getAttribute('aria-label'),'Double, calculated column, Sales');
  assert.equal(await finder.getAttribute('aria-activedescendant'),await options.first().getAttribute('id'),'the best match is under Enter');
  await finder.fill('zz-no-such-object');
  assert.match(await page.locator('#finder-list').innerText(),/No objects match “zz-no-such-object”/);
  assert.equal(await page.locator('#finder-count').innerText(),'No results');
  await page.locator('#finder-clear').click();
  assert.equal(await finder.inputValue(),'');
  assert.ok(await page.locator('#finder-clear').isHidden());
  assert.ok(await page.locator('#finder-list .finder-kind').count()>3,'clearing returns to the browse list');
  assert.ok(await finder.evaluate(el=>el===document.activeElement));
  step('typing narrows on every keystroke; results show name, kind and parent; count, clear button and empty state');
  // ---- keyboard
  await finder.fill('sales');
  const first=await finder.getAttribute('aria-activedescendant');
  await page.keyboard.press('ArrowDown');
  const second=await finder.getAttribute('aria-activedescendant');
  assert.notEqual(second,first);
  assert.equal(await page.locator('#'+second).getAttribute('aria-selected'),'true');
  await page.keyboard.press('ArrowUp');
  assert.equal(await finder.getAttribute('aria-activedescendant'),first);
  await page.keyboard.press('ArrowUp');
  assert.equal(await finder.getAttribute('aria-activedescendant'),await options.last().getAttribute('id'),'the list wraps');
  await page.keyboard.press('Escape');
  assert.equal(await finder.getAttribute('aria-expanded'),'false');
  assert.ok(await page.locator('#finder-panel').isHidden());
  assert.equal(await finder.inputValue(),'sales','Escape closes the list and keeps the text');
  assert.ok(await finder.evaluate(el=>el===document.activeElement));
  await page.locator('h1').first().click();
  await page.keyboard.press('/');
  assert.ok(await finder.evaluate(el=>el===document.activeElement),'"/" moves to the finder');
  step('arrow keys, Enter, Escape and the "/" shortcut');
  // ---- typing in the finder never changes a view's own filter; selecting handles a filter that hides the object
  await page.evaluate(()=>switchTab('tables'));
  await page.locator('#table-search').fill('Dim');
  assert.equal(await page.locator('#tbl-list details[data-table]:visible').count(),1);
  await finder.fill('Amount');
  assert.equal(await page.locator('#table-search').inputValue(),'Dim');
  assert.equal(await page.locator('#tbl-list details[data-table]:visible').count(),1,'the view is still filtered while the finder is used');
  const amount=await col('Sales','Amount');
  assert.ok(await page.locator(amount).isHidden(),'the object is hidden by the active filter');
  await page.locator('#finder-list [role=option]',{hasText:'Amount'}).filter({hasText:/^Amount/}).first().click();
  await landed(amount,'tables');
  assert.equal(await page.locator('#table-search').inputValue(),'','the filter that hid it is cleared');
  assert.match(await page.locator('#finder-note').innerText(),/Cleared the filter in this view to show Amount/);
  assert.ok(await page.locator(await page.evaluate(()=>'#tbl-'+slug('Sales'))).evaluate(el=>el.open),'its collapsed table is opened');
  assert.equal(await finder.getAttribute('aria-expanded'),'false');
  step('selecting a result reveals the object despite an active local filter and a collapsed container');
  // ---- an object in a view that is not shown (hidden tab), chosen from the keyboard
  await page.evaluate(()=>switchTab('measures'));
  await finder.fill('Unused');
  await page.keyboard.press('Enter');
  await landed(await col('Sales','Unused'),'tables');
  // a filter that does not hide the object is left alone
  await page.locator('#table-search').fill('Sales');
  await finder.fill('Region');
  await page.locator('#finder-list [role=option][aria-label="Region, column, Sales"]').click();
  await landed(await col('Sales','Region'),'tables');
  assert.equal(await page.locator('#table-search').inputValue(),'Sales');
  assert.equal(await page.locator('#finder-note').innerText(),'');
  step('an object in another view opens that view; a filter that does not hide it is kept');
  // ---- every kind of object lands on its own element
  const visit=async(text,kind,selector,tab)=>{
   await finder.fill(text);
   await page.locator(`#finder-list [role=option][aria-label*=", ${kind}"]`).first().click();
   await landed(selector,tab);
  };
  await visit('Total','measure',await page.evaluate(()=>'#mea-'+slug('Total')),'measures');
  assert.ok(await page.locator('details.measure[open] summary',{hasText:'Total'}).isVisible());
  await visit('Stage','query','#pq-title','power-query');
  assert.equal(await page.locator('#pq-title').innerText(),'Stage');
  await visit('Regional','security role',await page.evaluate(()=>'#role-'+slug('Regional')),'security');
  await visit('Dim','table',await page.evaluate(()=>'#tbl-'+slug('Dim')),'tables');
  await visit('server','data source',await page.evaluate(()=>'#'+sourceAnchor(searchObjects('server').groups.find(g=>g.kind==='data source').items[0].key)),'sources');
  assert.ok(await page.locator('#inspector').isVisible(),'a source opens with its details');
  await page.evaluate(()=>closeInspector());
  // a page selection that excludes the object is undone: Doubled is used on no page, so page p2 hides it
  await page.evaluate(()=>{switchTab('measures');setPageScope('p2');});
  assert.equal(await page.locator('#global-page').inputValue(),'p2');
  await finder.fill('Doubled');
  await page.locator('#finder-list [role=option][aria-label="Doubled, measure, Sales"]').click();
  await landed(await page.evaluate(()=>'#mea-'+slug('Doubled')),'measures');
  assert.equal(await page.locator('#global-page').inputValue(),'*');
  assert.match(await page.locator('#finder-note').innerText(),/Cleared the report page selection/);
  // a visual opens on its own page, selected, with its fields and filters beside the layout
  await page.evaluate(()=>showReportPage('p4'));
  await finder.fill('Table');
  await page.locator('#finder-list [role=option][data-object="pbi:visual:p1/v1"]').click();
  await landed(await page.evaluate(()=>'#'+visualBoxId('p1','v1')),'pages');
  assert.equal(await page.evaluate(()=>shownPage().id),'p1');
  assert.equal(await page.locator('#page-strip [aria-current="page"]').getAttribute('data-page'),'p1');
  assert.equal(await page.locator('#'+await page.evaluate(()=>visualBoxId('p1','v1'))).getAttribute('aria-pressed'),'true');
  assert.match(await page.locator('#page-panel').innerText(),/Clear selection/);
  // ...and one without coordinates lands on its row in the list of the page's visuals
  await finder.fill('Region slicer');
  await page.locator('#finder-list [role=option][data-object="pbi:visual:p5/u1"]').click();
  await landed(await page.evaluate(()=>'#'+visualAnchor('p5','u1')),'pages');
  assert.equal(await page.locator('#panel-visual').innerText(),'Region slicer');
  await finder.fill('Same');
  await page.locator('#finder-list [role=option][data-object="pbi:page:p2"]').click();
  await landed(await page.evaluate(()=>'#pg-'+pageKey('p2')),'pages');
  assert.equal(await page.locator('#page-strip [aria-current="page"]').getAttribute('data-page'),'p2');
  step('tables, columns, measures, queries, roles, sources, pages and visuals each land on their element');
  // ---- object links: a direct link after a reload, Back and Forward
  await page.evaluate(()=>switchTab('overview'));
  await finder.fill('Double');await page.keyboard.press('Enter');
  const double=await col('Sales','Double');
  await landed(double,'tables');
  const doubleHash=await page.evaluate(()=>location.hash);
  assert.equal(doubleHash,'#o/'+encodeURIComponent(await idOf('column','Sales','Double')));
  await finder.fill('Base');await page.keyboard.press('Enter');
  const base=await page.evaluate(()=>'#mea-'+slug('Base'));
  await landed(base,'measures');
  await page.goBack();
  await landed(double,'tables');
  assert.equal(await page.evaluate(()=>location.hash),doubleHash);
  await page.goBack();
  assert.equal(await page.evaluate(()=>activeTab),'overview');
  await page.goForward();await landed(double,'tables');
  await page.goForward();await landed(base,'measures');
  await page.reload();
  await landed(base,'measures');
  const fresh=await context.newPage();
  fresh.on('pageerror',error=>errors.push(error.message));
  await fresh.goto(url+doubleHash);
  await fresh.waitForFunction(s=>document.querySelector(s)?.classList.contains('obj-hit'),double);
  assert.equal(await fresh.evaluate(()=>activeTab),'tables');
  assert.ok(await fresh.locator(double).evaluate(el=>el===document.activeElement));
  await fresh.goto(url+'#o/'+encodeURIComponent('pbi:table:name:Removed since'));
  await fresh.waitForFunction(()=>document.getElementById('finder-note').textContent.includes('does not contain'));
  assert.equal(await fresh.evaluate(()=>activeTab),'overview');
  await fresh.goto(url+'#measures');await fresh.locator('#measure-search').waitFor();
  assert.equal(await fresh.evaluate(()=>activeTab),'measures','a link to a view still opens the view');
  await fresh.close();
  step('an object link opens the object after a reload, with Back and Forward, and in a new window');
  // ---- links inside the document use the same navigation
  await page.evaluate(()=>{switchTab('tables');});
  await page.locator('#table-search').fill('Dim');
  await page.evaluate(()=>switchTab('measures'));
  await page.locator(base).evaluate(el=>{el.open=true;});
  await page.locator(base).getByRole('button',{name:'Sales',exact:true}).first().click();
  await landed(await page.evaluate(()=>'#tbl-'+slug('Sales')),'tables');
  await page.goBack();
  assert.equal(await page.evaluate(()=>activeTab),'measures');
  step('table and measure links reveal their target and Back returns');
  // ---- Tables search: column names, the matching column named, clearing, and the empty state
  await page.evaluate(()=>switchTab('tables'));
  const search=page.locator('#table-search'),cards=page.locator('#tbl-list details[data-table]:visible');
  await search.fill('');
  const all=await cards.count();
  assert.equal(all,await page.evaluate(()=>countedTables().length),'every table but the automatic date tables, which are closed at the end');
  await search.fill('bookmarkon');
  assert.equal(await cards.count(),1);
  assert.match(await cards.first().locator('summary').innerText(),/^Sales\b/);
  assert.equal(await cards.first().locator('.tbl-match').innerText(),'Matching column: BookmarkOnly');
  assert.equal(await page.locator('#table-search-status').innerText(),'1 table shown');
  assert.deepEqual(await page.locator('#tbl-list .tbl-group:visible > h2').allInnerTexts().then(a=>a.map(t=>t.replace(/\s*\d+$/,''))),['Source tables'],'groups with no match go with their tables');
  // a match inside the closed automatic date tables opens them; clearing closes them again
  await search.fill('LocalDateTable_1f');
  assert.equal(await cards.count(),1);
  assert.ok(await page.locator('#tbl-group-auto').evaluate(el=>el.open));
  await search.fill('');
  assert.ok(!(await page.locator('#tbl-group-auto').evaluate(el=>el.open)));
  assert.equal(await cards.count(),all,'clearing restores the full list');
  assert.equal(await page.locator('#tbl-list .tbl-match:visible').count(),0);
  await search.fill('zz-no-such-thing');
  assert.equal(await cards.count(),0);
  assert.equal(await page.locator('#table-search-status').innerText(),'No matching tables or columns.');
  assert.ok(await page.locator('#tbl-results').isHidden());
  await search.fill('');
  step('Tables search matches column names, names the match, restores on clear and says when nothing matches');
  // ---- hub navigation: the library viewer protocol lands on the object, whatever was filtered inside
  const host=await context.newPage();
  host.on('pageerror',error=>errors.push(error.message));
  await host.goto(pathToFileURL(path.join(path.dirname(file),'pbidocgen-browser-host.html')).href);
  const frame=host.frameLocator('#doc');
  await frame.locator('#finder-input').waitFor();
  const hostHistory=await host.evaluate(()=>history.length);
  await host.evaluate(()=>send({type:'hello'}));
  await host.waitForFunction(()=>replies.some(r=>r.type==='viewer-ready'));
  const views=await host.evaluate(()=>replies.find(r=>r.type==='viewer-ready').views);
  for(const view of ['pbi.overview','pbi.table','pbi.measure','pbi.page','pbi.source']) assert.ok(views.includes(view),view);
  const inner=host.frames().find(f=>f.url().includes('/pbidocgen-browser.html'));
  await inner.evaluate(()=>{switchTab('tables');document.getElementById('table-search').value='Dim';fTbl('Dim');switchTab('overview');});
  const navigate=async(view_id,args)=>{
   const before=await host.evaluate(()=>replies.length);
   await host.evaluate(([view_id,args])=>send({type:'navigate-object',object_id:'x',view_id,args}),[view_id,args]);
   await host.waitForFunction(n=>replies.length>n,before);
   return host.evaluate(()=>replies.at(-1));
  };
  const hubLanded=async selector=>{
   await inner.waitForFunction(s=>document.querySelector(s)?.classList.contains('obj-hit'),selector);
   assert.ok(await inner.locator(selector).isVisible(),selector);
  };
  assert.equal((await navigate('pbi.table',{table:'Sales'})).exact,true);
  await hubLanded(await inner.evaluate(()=>'#tbl-'+slug('Sales')));
  assert.equal(await inner.evaluate(()=>activeTab),'tables');
  assert.equal((await navigate('pbi.measure',{table:'Sales',measure:'Total'})).exact,true);
  await hubLanded(await inner.evaluate(()=>'#mea-'+slug('Total')));
  assert.ok(await inner.locator('details.measure[open] summary',{hasText:'Total'}).isVisible());
  assert.equal((await navigate('pbi.page',{page:'p2'})).exact,true);
  await hubLanded(await inner.evaluate(()=>'#pg-'+pageKey('p2')));
  assert.equal((await navigate('pbi.source',{server:'server',database:'db',object:'dbo.Orders'})).exact,true);
  assert.equal(await inner.evaluate(()=>activeTab),'sources');
  assert.ok(await inner.locator('#inspector').isVisible());
  assert.equal((await navigate('pbi.table',{table:'No such table'})).exact,false);
  assert.equal((await navigate('pbi.overview',{})).exact,true);
  assert.equal(await inner.evaluate(()=>activeTab),'overview');
  assert.equal(await host.evaluate(()=>history.length),hostHistory,'a framed document never adds to the host history');
  await host.close();
  step('hub navigation (bi-doc-viewer) opens tables, measures, pages and sources on the object');
  // ---- narrow screens
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(()=>switchTab('overview'));
  await finder.fill('Amount');
  assert.ok(await options.first().isVisible());
  const panel=await page.locator('#finder-panel').boundingBox();
  assert.ok(panel.x>=0&&panel.x+panel.width<=390,'the result list fits a phone: '+JSON.stringify(panel));
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'the finder does not widen the page');
  await page.keyboard.press('Enter');
  await landed(await col('Sales','Amount'),'tables');
  step('the finder works at phone width');
  assert.deepEqual(errors,[]);
  console.log('Search and object navigation checks passed in Chromium.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
