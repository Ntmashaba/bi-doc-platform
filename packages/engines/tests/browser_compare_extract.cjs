// "Compare extracts" in real Chromium with a baseline written by `bidoc generate --also json`.
//   node browser_compare_extract.cjs <current document.html> <earlier extract.json>
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const [html,json]=process.argv.slice(2);
(async()=>{
  const browser=await chromium.launch(process.env.CHROMIUM_PATH?{headless:true,executablePath:process.env.CHROMIUM_PATH}:{headless:true});
  try{
    const page=await browser.newPage();
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto('file://'+html);
    await page.evaluate(()=>switchTab('compare'));
    await page.locator('#main input[type=file]').setInputFiles(json);
    const main=page.locator('#main');
    await main.getByText('Sales[Revenue]',{exact:false}).first().waitFor();
    let text=await main.innerText();
    assert.doesNotMatch(text,/Could not compare this file/,'the bidoc extract is accepted as a baseline');
    assert.match(text,/1 changes in this scope/,'exactly the one change');
    assert.match(text,/Changed\s+Measure\s+Sales\[Revenue\]/,'named as a changed measure');
    await main.getByText('Before / after').first().click();
    text=await main.innerText();
    assert.match(text,/SUM\(Sales\[Amount\]\)/,'the earlier definition is shown');
    assert.match(text,/SUMX\(Sales, Sales\[Amount\] \* 1\.15\)/,'beside the current one');
    assert.deepEqual(errors,[],'no script errors');
    console.log('Compare extracts: a bidoc --also json extract is a working baseline; the changed measure is listed.');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
