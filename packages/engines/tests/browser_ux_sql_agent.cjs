// UX rework, step 1: characterization of the SQL Agent document behaviour the rework must keep (real Chromium).
// Run through check_ux_browser.py: node browser_ux_sql_agent.cjs <sql agent document.html>
// Pins: every section opens and marks itself current, a job link opens that job (and a step link its step),
// the overview keeps its reading guide and review-first list, and the 390 px menu opens, reports its
// state and closes when a section is chosen, with no sideways scroll.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),path=require('node:path');
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
 const url=pathToFileURL(path.resolve(process.argv[2])).href;
 try{
  const page=await open({width:1440,height:900});
  await page.goto(url);
  assert.equal(await page.getAttribute('#sec-overview','aria-current'),'page');
  const main=await page.locator('#main').innerText();
  assert.match(main,/How to read this document/);
  assert.match(main,/Review first/);
  step('the overview opens first, with its reading guide and review-first list');
  const sections=await page.evaluate(()=>SECTIONS.map(s=>s.id));
  for(const id of sections){
   await page.click('#sec-'+id);
   assert.equal(await page.getAttribute('#sec-'+id,'aria-current'),'page','section '+id+' is current');
   assert.ok((await page.locator('#main').innerText()).trim().length>0,'section '+id+' renders');
  }
  step('every section opens and marks itself current ('+sections.length+')');
  await page.click('#sec-jobs');
  const link=page.locator('#main [data-go=job]').first();
  const job=await link.getAttribute('data-arg');
  await link.click();
  assert.equal(await page.evaluate(()=>SEL_JOB),job);
  assert.equal(await page.getAttribute('#sec-job','aria-current'),'page');
  assert.ok(await page.locator('#jobcard').isVisible(),'the job card shows');
  step('choosing a job in the list opens it in Selected job');

  const phone=await open({width:390,height:844});
  await phone.goto(url);
  const toggle=phone.locator('#navtoggle');
  assert.ok(await toggle.isVisible(),'the menu button shows at 390 px');
  assert.equal(await toggle.getAttribute('aria-expanded'),'false');
  await toggle.click();
  assert.equal(await toggle.getAttribute('aria-expanded'),'true');
  await phone.click('#sec-issues');
  assert.equal(await toggle.getAttribute('aria-expanded'),'false','choosing a section closes the menu');
  assert.match(await toggle.innerText(),/Review issues/,'the button names the open section');
  await phone.click('#navtoggle');await phone.click('#sec-overview');
  const width=await phone.evaluate(()=>document.documentElement.scrollWidth);
  assert.ok(width<=390,'no sideways scroll at 390 px: '+width);
  step('at 390 px the menu opens, closes on choosing a section, names it, and nothing scrolls sideways');

  const widths=await checkMobileNav(browser,url,{toggle:'#navtoggle',label:'#navtoggle .nt-label',sectionButtons:'.nav-btn',section:page=>page.evaluate(()=>document.querySelector('#nav-sections [aria-current]').childNodes[0].textContent.trim())});
  step('the narrow-screen menu works by keyboard, Escape and focus at '+widths.join(' and ')+' px, and stays out of the way on a wide screen');
  assert.deepEqual(errors,[],'no page errors');
  step('no page errors');
 }finally{ await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
