// A manifest-bearing document in real Chromium: its details are read-only and nothing in the page saves a copy.
//   node browser_sealed_details.cjs <document.html> <power_bi|adf>
// check_sealed_browser.py validates the file before and after, so the round trip ends where the reported
// HASH_MISMATCH began: the artifact the library is given.
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const [file,engine]=process.argv.slice(2);
const tab=engine==='power_bi'?'report-details':'details';
const save=engine==='power_bi'?'saveDocumentationHtml':'saveDetailsHtml';
(async()=>{
  const browser=await chromium.launch(process.env.CHROMIUM_PATH?{headless:true,executablePath:process.env.CHROMIUM_PATH}:{headless:true});
  try{
    const page=await browser.newPage({acceptDownloads:true});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    const downloads=[];page.on('download',d=>downloads.push(d.suggestedFilename()));
    await page.goto('file://'+file);
    assert.equal(await page.locator('#pbidoc-manifest').count(),1,'the document carries the manifest');
    await page.evaluate(t=>switchTab(t),tab);
    const main=page.locator('#main');
    await main.getByText('Read-only.',{exact:false}).waitFor();
    assert.match(await main.innerText(),/This copy is protected against changes, so its details cannot be edited here\. They are recorded when the documentation is generated\./);
    assert.equal(await main.locator('input, textarea').count(),0,'no field to type in');
    assert.equal(await page.getByRole('button',{name:'Download updated HTML'}).count(),0,'no save button');
    assert.doesNotMatch(await main.innerText(),/survive regeneration|replace the original/,'no saving instructions');
    await page.evaluate(f=>window[f](),save);           // the save function, called directly, writes nothing
    await page.waitForTimeout(500);
    assert.deepEqual(downloads,[],'no copy was offered for download');
    assert.deepEqual(errors,[],'no script errors');
    console.log(`${engine}: read-only in Chromium; nothing saved.`);
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
