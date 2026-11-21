// UX rework, step 1: characterization of the ADF document behaviour the rework must keep (real Chromium).
// Run through check_ux_browser.py: node browser_ux_adf.cjs <adf document.html>
// Pins: every section opens its first available view and remembers the last one, an object chosen anywhere
// opens on Objects with its detail beside the list, the library viewer protocol lands on pipelines and
// triggers, and at 390 px every section stays reachable with no sideways scroll.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs'),os=require('node:os');
const {pathToFileURL}=require('node:url');
const step=name=>console.log('  ok '+name);
(async()=>{
 const browser=await chromium.launch(process.env.CHROMIUM_PATH?{headless:true,executablePath:process.env.CHROMIUM_PATH}:{headless:true});
 const errors=[];
 const open=async viewport=>{
  const page=await (await browser.newContext({viewport})).newPage();
  page.on('pageerror',error=>errors.push(error.message));
  page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  return page;
 };
 const file=path.resolve(process.argv[2]),url=pathToFileURL(file).href;
 const current=page=>page.evaluate(()=>CURRENT);
 try{
  const page=await open({width:1440,height:900});
  await page.goto(url);
  assert.equal(await current(page),'overview');
  // ---- sections and their views
  const sections=await page.evaluate(()=>SECTIONS.map(s=>s.id));
  for(const id of sections){
   await page.click('#sec-'+id);
   const first=await page.evaluate(id=>TABS.find(t=>t.section===id&&t.avail()).id,id);
   assert.equal(await current(page),first,'section '+id+' opens '+first);
   assert.equal(await page.getAttribute('#sec-'+id,'aria-current'),'page');
  }
  const second=await page.evaluate(()=>TABS.filter(t=>t.section==='pipelines'&&t.avail())[1].id);
  await page.click('#sec-pipelines');
  await page.click('#nav-'+second);
  await page.click('#sec-overview');
  await page.click('#sec-pipelines');
  assert.equal(await current(page),second,'a section reopens on the view last used in it');
  step('each section opens its first view, marks itself current and remembers the last view used');

  // ---- object selection: from anywhere to Objects, with the detail beside the list
  const key=await page.evaluate(()=>DATA.entities.find(e=>PHYSICAL.has(e.kind)).key);
  await page.evaluate(k=>goEnt(k),key);
  assert.equal(await current(page),'objects');
  const id=await page.evaluate(k=>'obj-'+slug(k),key);
  assert.ok(await page.locator('#'+id).evaluate(el=>el.classList.contains('sel')),'the chosen row is marked');
  const label=await page.evaluate(k=>EIDX[k].label,key);
  assert.ok((await page.locator('#obj-panel').innerText()).includes(label),'the detail names the object');
  const other=await page.evaluate(k=>DATA.entities.find(e=>PHYSICAL.has(e.kind)&&e.key!==k)?.key,key);
  if(other){
   await page.click('#obj-'+(await page.evaluate(k=>slug(k),other))+' button[data-go=obj]');
   assert.equal(await page.evaluate(()=>SEL_OBJ),other,'choosing in the list moves the selection');
   assert.equal(await page.locator('#obj-table tr.sel').count(),1);
  }
  step('an object chosen elsewhere opens on Objects, selected, with its detail beside the list');

  // ---- step 2: no empty detail panel; the list takes the width until an object is chosen
  await page.evaluate(()=>{SEL_OBJ=null;switchTab('objects');});
  assert.equal(await page.locator('#obj-panel').count(),0,'no panel without a selection');
  assert.equal(await page.locator('#obj-table tr.sel').count(),0,'nothing is chosen for the reader');
  const full=await page.locator('#obj-table').boundingBox(),split=await page.locator('#obj-split').boundingBox();
  assert.ok(full.width>=split.width-2,'the list fills the width: '+full.width+' of '+split.width);
  await page.locator('#obj-table button[data-go=obj]').first().click();
  assert.equal(await page.locator('#obj-panel').count(),1,'choosing inserts the panel');
  assert.ok((await page.locator('#obj-table').boundingBox()).width<full.width-200,'the list makes room for it');
  assert.match(await page.locator('#obj-status').textContent(),/^Showing /,'the change is announced');
  assert.equal(await page.locator('#obj-status').getAttribute('aria-live'),'polite');
  await page.locator('#obj-panel [data-go=closeobj]').click();
  assert.equal(await page.locator('#obj-panel').count(),0,'Close removes the panel');
  assert.equal(await page.evaluate(()=>SEL_OBJ),null);
  assert.equal(await page.locator('#obj-status').textContent(),'Detail closed');
  assert.ok(await page.evaluate(()=>document.activeElement?.closest('#obj-table')!==null),'focus returns to the list');
  await page.evaluate(k=>goEnt(k),key);
  assert.equal(await page.locator('#obj-panel').count(),1,'a link from elsewhere still opens the detail');
  step('Objects shows the full-width list until an object is chosen, then its detail; Close restores the list');

  // ---- the library viewer protocol, from a framing page
  const host=path.join(fs.mkdtempSync(path.join(os.tmpdir(),'adf-ux-')),'host.html');
  fs.writeFileSync(host,`<!DOCTYPE html><iframe id="f" src="${url}" style="width:1200px;height:800px"></iframe>
<script>window.replies=[];addEventListener('message',e=>{if(e.data&&e.data.protocol==='bi-doc-viewer')replies.push(e.data);});
function send(m){document.getElementById('f').contentWindow.postMessage(Object.assign({protocol:'bi-doc-viewer',version:1,channel:'c1',revision_id:'r1'},m),'*');}</script>`);
  const framed=await open({width:1300,height:900});
  await framed.goto(pathToFileURL(host).href);
  const frame=()=>framed.frame({url});
  // The framed document is another origin (file://), so its state is read through Playwright's frame.
  await framed.waitForLoadState('load');
  await frame().waitForFunction(()=>typeof CURRENT!=='undefined'&&CURRENT==='overview');
  await framed.evaluate(()=>send({type:'hello'}));
  await framed.waitForFunction(()=>replies.some(r=>r.type==='viewer-ready'));
  const views=await framed.evaluate(()=>replies.find(r=>r.type==='viewer-ready').views);
  assert.deepEqual(views,['adf.overview','adf.pipeline','adf.activity','adf.trigger','adf.dataflow']);
  const pipeline=await frame().evaluate(()=>DATA.pipelines[0].name);
  await framed.evaluate(p=>send({type:'navigate-object',view_id:'adf.pipeline',object_id:'x',args:{pipeline:p}}),pipeline);
  await framed.waitForFunction(()=>replies.some(r=>r.type==='navigated'));
  assert.deepEqual(await framed.evaluate(()=>replies.filter(r=>r.type==='navigated').map(r=>r.exact)),[true]);
  assert.equal(await frame().evaluate(()=>CURRENT),'pipelines');
  await framed.evaluate(()=>send({type:'navigate-object',view_id:'adf.pipeline',object_id:'y',args:{pipeline:'no such pipeline'}}));
  await framed.waitForFunction(()=>replies.filter(r=>r.type==='navigated').length===2);
  assert.equal(await framed.evaluate(()=>replies.filter(r=>r.type==='navigated')[1].exact),false);
  step('framed by the library, the document answers hello and lands on a pipeline (exact) or reports a miss');
  await framed.context().close();

  // ---- narrow screens: every section reachable, nothing wider than the screen
  const phone=await open({width:390,height:844});
  await phone.goto(url);
  for(const id of sections){
   await phone.click('#sec-'+id);
   assert.ok(await phone.evaluate(id=>TABS.find(t=>t.id===CURRENT).section===id,id),'section '+id+' opens at 390 px');
  }
  await phone.click('#sec-overview');
  const width=await phone.evaluate(()=>document.documentElement.scrollWidth);
  assert.ok(width<=390,'no sideways scroll on the overview at 390 px: '+width);
  step('at 390 px every section opens and the overview does not scroll sideways');

  assert.deepEqual(errors,[],'no page errors');
  step('no page errors');
 }finally{ await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
