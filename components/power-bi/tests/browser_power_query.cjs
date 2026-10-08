// The Power Query view in real Chromium (UI rework, Change 3).
// Run through check_browser.py: node browser_power_query.cjs <pbidocgen-browser.html>
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
 const title=page.locator('#pq-title'),detail=page.locator('#pq-detail'),finder=page.locator('#finder-input');
 const item=name=>page.locator('.pq-item',{has:page.locator('.pq-name',{hasText:new RegExp('^'+name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'$')})});
 const shown=async name=>{await page.waitForFunction(n=>document.getElementById('pq-title')?.textContent===n,name);};
 const status=async label=>(await detail.locator('.pq-status',{has:page.locator('dt',{hasText:label})}).locator('.badge').innerText()).trim();
 try{
  await page.goto(url);
  await page.locator('#sec-power-query').click();
  assert.equal(await page.evaluate(()=>activeTab),'power-query');
  assert.match(await page.locator('#view-line').innerText(),/^Power Query\. The Power Query Editor in Power BI Desktop/);
  // ---- the queries pane: folders as recorded, a nested folder under its parent, the rest under "Other Queries"
  assert.deepEqual((await page.locator('.pq-folder').allInnerTexts()).map(t=>t.replace(/\s*\d+$/,'')),['Staging','Parameters','Other Queries']);
  const indent=await page.locator('.pq-folder').evaluateAll(els=>els.map(el=>parseFloat(getComputedStyle(el).paddingLeft)));
  assert.ok(indent[1]>indent[0]&&indent[2]===indent[0],'the folder inside Staging is indented: '+indent);
  assert.deepEqual(await page.locator('.pq-item .pq-name').allInnerTexts(),['Stage','Region','Sales','fnClean','Dim','Dynamic','Native','Cut off']);
  assert.equal(await item('Stage').locator('.pq-name').evaluate(el=>getComputedStyle(el).fontStyle),'italic','a query that is not loaded is in italics');
  assert.equal(await item('Sales').locator('.pq-name').evaluate(el=>getComputedStyle(el).fontStyle),'normal');
  assert.equal(await item('fnClean').locator('.pq-kind').innerText(),'fx');
  assert.equal(await item('Region').locator('.pq-kind').innerText(),'param');
  await shown('Stage');
  assert.equal(await item('Stage').getAttribute('aria-current'),'true','the first query is open to start with');
  step('queries pane: folders in file order, nesting, load status, function and parameter marks');
  // ---- one query: header, three statuses, steps, and the script collapsed until asked for
  await item('Sales').click();
  await shown('Sales');
  assert.equal(await item('Sales').getAttribute('aria-current'),'true');
  assert.equal(await item('Stage').getAttribute('aria-current'),'false');
  const facts=Object.fromEntries(await detail.locator('.pq-facts tr').evaluateAll(rows=>rows.map(r=>[r.cells[0].innerText.trim(),r.cells[1].innerText.replace(/\s+/g,' ').trim()])));
  assert.match(facts['Loads table'],/^Sales/);
  assert.match(facts['Load status'],/^loaded/);
  assert.equal(facts['Upstream queries'],'fnClean, Stage');
  assert.match(facts['External sources'],/SQL Server · server \/ db · dbo\.Orders/);
  assert.deepEqual([await status('Expression extraction'),await status('Applied Steps'),await status('Publication')],['Complete','Parsed','Included']);
  assert.deepEqual(await detail.locator('.pq-step-name').allInnerTexts(),['S','T','Cleaned','Tagged, with Stage']);
  // Each step says what it is written to do where its form is recognised, and opens to its own expression.
  assert.deepEqual(await detail.locator('.pq-steps > li').evaluateAll(items=>items.map(li=>li.querySelector('.pq-step-says')?.textContent||'')),
   ['Connects to SQL Server: server server, database db','Navigates to dbo.Orders','Invokes the function fnClean','Adds the column Tag']);
  const third=detail.locator('.pq-steps > li').nth(2).locator('details');
  assert.ok(await third.locator('pre').isHidden(),'a step is closed until asked for');
  await third.locator('summary').click();
  assert.equal(await third.locator('pre').innerText(),'fnClean(T)');
  await third.locator('summary').focus();await page.keyboard.press('Enter');
  assert.ok(await third.locator('pre').isHidden(),'a step opens and closes from the keyboard');
  assert.match(await detail.locator('.pq-steps-lead').innerText(),/^In the order written\. Each step is described from its text\./);
  const code=detail.locator('#pq-code');
  assert.equal(await code.evaluate(el=>el.open),false);
  assert.ok(await code.locator('pre').isHidden(),'the script is collapsed');
  await code.locator('summary').click();
  assert.equal(await code.locator('pre').innerText(),await page.evaluate(()=>DATA.sourceQueries.find(q=>q.queryName==='Sales').mCode));
  step('a query shows its header, three statuses, Applied Steps in order and the full script on request');
  await item('Stage').click();await shown('Stage');
  assert.equal((await detail.locator('.pq-facts tr',{hasText:'Used by'}).locator('td').innerText()).replace(/\s+/g,' '),'Dim, Lake, Sales');
  assert.match(await detail.locator('.pq-desc').innerText(),/every table query starts from/);
  await item('Cut off').click();await shown('Cut off');
  assert.deepEqual([await status('Expression extraction'),await status('Applied Steps'),await status('Publication')],['Known partial','Unsupported syntax','Included']);
  await item('Region').click();await shown('Region');
  assert.equal(await status('Applied Steps'),'No top-level Applied Steps');
  await item('fnClean').click();await shown('fnClean');
  assert.deepEqual(await detail.locator('.pq-step-name').allInnerTexts(),['Trimmed','Kept Rows']);
  assert.match(await detail.innerText(),/these are the steps of its body/);
  step('a query used by several tables lists them once; partial, step-less and function queries report their own status');
  // ---- links: an upstream query, and the table a query loads
  await item('Sales').click();await shown('Sales');
  await detail.locator('.pq-facts button',{hasText:'fnClean'}).click();
  await shown('fnClean');
  assert.equal(await page.evaluate(()=>location.hash),'#o/'+encodeURIComponent('pbi:query:name:fnClean'));
  await page.goBack();await shown('Sales');
  await page.goForward();await shown('fnClean');
  await page.goBack();await shown('Sales');
  await detail.locator('.pq-facts button',{hasText:'Sales'}).first().click();
  await page.waitForFunction(()=>activeTab==='tables');
  assert.ok(await page.locator(await page.evaluate(()=>'#tbl-'+slug('Sales'))).evaluate(el=>el.open));
  await page.goBack();await shown('Sales');
  assert.equal(await page.evaluate(()=>activeTab),'power-query');
  step('upstream queries and the loaded table are links; Back and Forward move between queries');
  // ---- the pane's own filter, and the finder revealing a query that filter hides
  const filter=page.locator('#pq-filter');
  await filter.fill('sta');
  assert.deepEqual(await page.locator('.pq-item:visible .pq-name').allInnerTexts(),['Stage']);
  assert.deepEqual(await page.locator('.pq-folder:visible').allInnerTexts().then(a=>a.map(t=>t.replace(/\s*\d+$/,''))),['Staging']);
  assert.equal(await page.locator('#pq-filter-status').innerText(),'1 query shown');
  await filter.fill('zz-none');
  assert.equal(await page.locator('#pq-filter-status').innerText(),'No matching queries.');
  assert.equal(await page.locator('.pq-item:visible').count(),0);
  await filter.fill('sta');
  await finder.fill('fnCl');
  assert.equal(await filter.inputValue(),'sta','typing in the finder leaves the pane filter alone');
  await page.locator('#finder-list [role=option][aria-label^="fnClean, query"]').click();
  await shown('fnClean');
  await page.waitForFunction(()=>document.getElementById('pq-title').classList.contains('obj-hit'));
  assert.ok(await title.evaluate(el=>el===document.activeElement),'the query has keyboard focus');
  assert.equal(await filter.inputValue(),'','the filter that hid the query is cleared');
  assert.match(await page.locator('#finder-note').innerText(),/Cleared the filter in this view to show fnClean/);
  assert.ok(await item('fnClean').isVisible());
  // a filter that does not hide the query stays
  await filter.fill('s');
  await finder.fill('Stage');
  await page.locator('#finder-list [role=option][aria-label^="Stage, query"]').click();
  await shown('Stage');
  assert.equal(await filter.inputValue(),'s');
  await filter.fill('');
  step('the pane filter narrows by name; the finder reveals a query the filter hides and leaves other filters alone');
  // ---- a direct link after a reload, and from another view
  const link=url+'#o/'+encodeURIComponent('pbi:query:name:Sales');
  await page.goto(link);await shown('Sales');
  await page.reload();await shown('Sales');
  assert.equal(await page.evaluate(()=>activeTab),'power-query');
  const stage=await page.evaluate(()=>objectFor('query','Stage').id);
  assert.equal(stage,'pbi:query:7a000000-0000-4000-8000-000000000001','a shared query is identified by its lineage tag');
  await page.evaluate(()=>switchTab('measures'));
  await page.evaluate(id=>goObject(id),stage);await shown('Stage');
  assert.equal(await page.evaluate(()=>activeTab),'power-query');
  // Sources points here for scripts
  await page.evaluate(()=>switchTab('sources'));
  await page.locator('#main button.xl',{hasText:/^Power Query$/}).click();
  assert.equal(await page.evaluate(()=>activeTab),'power-query');
  await shown('Stage');
  step('an object link opens the query after a reload and from any view');
  // ---- the library viewer protocol
  const host=await context.newPage();
  host.on('pageerror',error=>errors.push(error.message));
  await host.goto(pathToFileURL(path.join(path.dirname(file),'pbidocgen-browser-host.html')).href);
  await host.frameLocator('#doc').locator('#finder-input').waitFor();
  await host.evaluate(()=>send({type:'hello'}));
  await host.waitForFunction(()=>replies.some(r=>r.type==='viewer-ready'));
  assert.ok((await host.evaluate(()=>replies.find(r=>r.type==='viewer-ready').views)).includes('pbi.query'));
  const inner=host.frames().find(f=>f.url().includes('/pbidocgen-browser.html'));
  await inner.evaluate(()=>{switchTab('power-query');document.getElementById('pq-filter').value='zz';pqFilter();switchTab('overview');});
  const before=await host.evaluate(()=>replies.length);
  await host.evaluate(()=>send({type:'navigate-object',object_id:'q',view_id:'pbi.query',args:{query:'fnClean'}}));
  await host.waitForFunction(n=>replies.length>n,before);
  assert.equal((await host.evaluate(()=>replies.at(-1))).exact,true);
  await inner.waitForFunction(()=>document.getElementById('pq-title')?.textContent==='fnClean');
  assert.ok(await inner.locator('#pq-title').isVisible());
  await host.close();
  step('hub navigation (bi-doc-viewer) opens a query, whatever was filtered inside');
  // ---- narrow screens
  await page.setViewportSize({width:390,height:844});
  await page.goto(link);await shown('Sales');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'the view fits a phone without sideways scrolling');
  const pane=await page.locator('.pq-pane').boundingBox(),body=await detail.boundingBox();
  assert.ok(pane.y+pane.height<=body.y+1,'the queries pane sits above the query on a phone');
  await page.waitForFunction(()=>document.getElementById('pq-title').classList.contains('obj-hit'));
  const box=await title.boundingBox();
  assert.ok(box.y>=0&&box.y<844,'the linked query is on screen: '+JSON.stringify(box));
  step('the view works at phone width');
  assert.deepEqual(errors,[]);
  console.log('Power Query view checks passed in Chromium.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
