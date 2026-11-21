// UX rework, step 1: characterization of the ADF document behaviour the rework must keep (real Chromium).
// Run through check_ux_browser.py: node browser_ux_adf.cjs <adf document.html>
// Pins: every section opens its first available view and remembers the last one, an object chosen anywhere
// opens on Objects with its detail beside the list, the library viewer protocol lands on pipelines and
// triggers, and at 390 px every section stays reachable with no sideways scroll.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs'),os=require('node:os');
const {pathToFileURL}=require('node:url');
const step=name=>console.log('  ok '+name);
const {checkMobileNav}=require('./ux_mobile_nav.cjs');
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
  // ---- step 4 overview: title and summary, Review first, how work starts, grouped counts, then collapsed details
  const order=await page.evaluate(()=>[...document.querySelectorAll('#main > *')].map(e=>e.id||['overview-summary','review-first','count-group','table-wrap'].find(c=>e.classList.contains(c))||e.tagName).filter((x,i,a)=>x!==a[i-1]));
  const at=x=>order.indexOf(x);
  assert.ok(at('H1')===0&&at('overview-summary')===1&&at('review-first')===2,'title, summary, then Review first: '+order);
  assert.ok(at('review-first')<at('contents')&&at('contents')<at('count-group')&&at('count-group')<at('coverage-details')&&at('coverage-details')<at('doc-details'),'then Contents and the collapsed details: '+order);
  const limit=await page.locator('.reading-limit').innerText();
  for(const kept of [/no run history/,/runs, fails or is used/,/never value/,/outside ADF \(REST API, Synapse, Logic Apps\)/]) assert.match(limit,kept,'the short reading limit keeps '+kept);
  for(const id of ['coverage-details','doc-details']) assert.equal(await page.locator('#'+id).getAttribute('open'),null,id+' starts collapsed');
  assert.equal(await page.locator('#doc-details #generated-line').count(),1);
  const gaps=await page.evaluate(()=>coverageGaps());
  if(gaps.length){
   assert.match(await page.locator('#coverage-details > summary').textContent(),new RegExp(gaps[0].replace(/[()]/g,'\\$&')),'the closed coverage summary still names the gaps');
   await page.locator('.coverage-gaps button').click();
   assert.notEqual(await page.locator('#coverage-details').getAttribute('open'),null,'Review first opens the coverage table');
  }
  // Counts that look like links are links, open their view, and match its own count; the rest are plain.
  for(const id of await page.locator('#main .stat-link').evaluateAll(els=>els.map(e=>e.id.replace('count-','')))){
   await page.evaluate(()=>switchTab('overview'));
   const n=Number((await page.locator('#count-'+id+' .big').innerText()).replace(/,/g,''));
   await page.click('#count-'+id);
   assert.equal(await current(page),id,'the count opens '+id);
   assert.equal(await page.evaluate(id=>TABS.find(t=>t.id===id).count(),id),n,id+' shows the number it counted');
  }
  await page.evaluate(()=>switchTab('overview'));
  assert.equal(await page.locator('#main .stat:not(.stat-link) button, #main div.stat[data-go]').count(),0,'a plain figure is not clickable');
  step('the overview reads review-first, keeps the reading limits, and its counts open the lists they count');
  await page.goto(url);   // a fresh document: the counts above set each section's last view

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
   await phone.click('#navtoggle');
   await phone.click('#sec-'+id);
   assert.ok(await phone.evaluate(id=>TABS.find(t=>t.id===CURRENT).section===id,id),'section '+id+' opens from the menu at 390 px');
  }
  await phone.click('#navtoggle');
  await phone.click('#sec-overview');
  const width=await phone.evaluate(()=>document.documentElement.scrollWidth);
  assert.ok(width<=390,'no sideways scroll on the overview at 390 px: '+width);
  step('at 390 px every section opens from the menu and the overview does not scroll sideways');

  const widths=await checkMobileNav(browser,url,{toggle:'#navtoggle',label:'#navtoggle .nt-label',sectionButtons:'.nav-btn',section:page=>page.evaluate(()=>document.querySelector('#nav-sections [aria-current]').childNodes[0].textContent.trim())});
  step('the narrow-screen menu works by keyboard, Escape and focus at '+widths.join(' and ')+' px, and stays out of the way on a wide screen');
  assert.deepEqual(errors,[],'no page errors');
  step('no page errors');
 }finally{ await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
