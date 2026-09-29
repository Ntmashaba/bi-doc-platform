// Real Chromium gate. Requires Playwright and its Chromium binary.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs'),os=require('node:os');
const {pathToFileURL}=require('node:url');
const {execFileSync}=require('node:child_process');
(async()=>{
 const browser=await chromium.launch({headless:true});
 const context=await browser.newContext({acceptDownloads:true,viewport:{width:1440,height:1000}});
 const page=await context.newPage();const errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 try{
  await page.goto(pathToFileURL(path.resolve(process.argv[2])).href);
  // Six sections in the rail; each view is a sub-tab inside its section.
  const openTab=async id=>{const sec=await page.evaluate(id=>sectionOf(id).id,id);
   if(await page.locator('#nav-toggle').isVisible()) await page.locator('#nav-toggle').click();
   await page.locator('#sec-'+sec).click();
   if(await page.locator('#nav-'+id).count()) await page.locator('#nav-'+id).click();
   assert.equal(await page.evaluate(()=>activeTab),id);};
  assert.equal(await page.evaluate(()=>activeTab),'overview');
  assert.equal(await page.locator('nav .nav-btn').count(),6);
  for(const id of await page.evaluate(()=>TABS.filter(t=>t.avail&&!t.hidden).map(t=>t.id))){await openTab(id);}
  await openTab('columns');await page.locator('#column-search').fill('Amount');
  await page.locator('#global-page').selectOption('p2');await openTab('tables');
  assert.equal(await page.locator('#global-page').inputValue(),'p2');
  await openTab('columns');assert.equal(await page.locator('#column-search').inputValue(),'Amount');
  assert.equal(await page.locator('#column-rows tr').count(),1);
  await page.locator('#global-page').selectOption('*');
  await openTab('impact');
  await page.locator('#impact-field').selectOption('["c","Sales","Amount"]');
  await openTab('cleanup');
  await page.evaluate(()=>inspectNode(nodeId('m','Sales','Total')));
  await page.locator('#inspector').getByRole('button',{name:'Close details',exact:true}).click();
  await openTab('impact');
  assert.equal(await page.locator('#impact-field').inputValue(),'["m","Sales","Total"]');
  assert.match(await page.locator('#impact-details').innerText(),/Sales\[Total\]/);
  await openTab('lineage');
  // SVG labels are truncated; match its safely encoded full handler instead.
  // Selecting a node toggles the lineage focus; the hostile name must stay inert.
  await page.locator('#lineage-board g[onclick*="reviewMarker"]').click();
  assert.equal(await page.evaluate(()=>lineageFocus?.key),"x');globalThis.reviewMarker=1;//");
  await page.locator('#lineage-board g[onclick*="reviewMarker"]').click();
  assert.equal(await page.evaluate(()=>lineageFocus),null);
  assert.equal(await page.evaluate(()=>globalThis.reviewMarker),undefined);
  await openTab('rels');
  const names=await page.evaluate(()=>[slug('Sales-US'),slug('Sales US')]);assert.notEqual(names[0],names[1]);
  await openTab('layout');await page.locator('.visual-box').first().click();
  assert.ok(await page.locator('#inspector').isVisible());await page.keyboard.press('Tab');
  await page.locator('#inspector').getByRole('button',{name:'Close details',exact:true}).click();
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'pbi-browser-'));
  const exports=[['columns','Export filtered CSV','column-page-usage.csv'],['tables','Export source summary CSV','report-table-sources.csv'],['sources','Export all M queries CSV','source-queries.csv'],['sources','Export source objects CSV (with code)','source-objects.csv'],['sources','Export source objects CSV (no code)','source-objects-no-code.csv'],['primary-sources','Export primary sources CSV','primary-sources.csv'],['cleanup','Export evidence at column/page grain','cleanup-column-page-usage.csv']];
  for(const [tab,label,suffix] of exports){
   await openTab(tab);
   // Detailed inventories sit in collapsed sections; expand the one holding the button.
   await page.evaluate(label=>{for(const b of document.querySelectorAll('#main button')) if(b.textContent.trim()===label) for(let d=b.closest('details');d;d=d.parentElement.closest('details')) d.open=true;},label);
   const pending=page.waitForEvent('download');await page.getByRole('button',{name:label,exact:true}).click();const download=await pending;
   assert.equal(download.suggestedFilename(),'Sales-'+suffix);
   const file=path.join(tmp,suffix);await download.saveAs(file);
   if(suffix==='source-queries.csv') execFileSync('python',['-c','import csv,sys; f=open(sys.argv[1],encoding="utf-8-sig",newline=""); r=csv.reader(f); assert next(r)==["report","query name","query m code"]; assert any(row[1]=="Stage" and "\\n" in row[2] for row in r)',file]);
  }
  const baseline=JSON.parse(fs.readFileSync('/tmp/pbidocgen-browser.json','utf8'));baseline.model.expressions[0].expression='changed';
  // Comparison is an advanced route, intentionally absent from navigation.
  await page.goto(page.url().split('#')[0]+'#compare');await page.locator('input[type=file]').setInputFiles({name:'before.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(baseline))});
  await page.locator('tbody th').filter({hasText:'Shared expression'}).waitFor();
  const pending=page.waitForEvent('download');await page.getByRole('button',{name:'Export changes by page',exact:true}).click();
  assert.equal((await pending).suggestedFilename(),'Sales-extract-changes-by-page.csv');
  await page.setViewportSize({width:768,height:900});await openTab('matrix');
  assert.ok(await page.locator('.matrix').isVisible());
  // Narrow screens: sections sit behind the menu button, which names the current section.
  await page.setViewportSize({width:390,height:844});
  assert.ok(!(await page.locator('#sec-overview').isVisible()));
  assert.equal((await page.locator('#nav-current').innerText()).trim(),'Impact & usage');
  await openTab('overview');assert.ok(!(await page.locator('#sec-overview').isVisible()));
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  assert.deepEqual(errors,[]);
  fs.rmSync(tmp,{recursive:true,force:true});console.log('Chromium regression checks passed.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
