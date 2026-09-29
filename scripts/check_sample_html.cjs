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
