// Real-browser offline save/reopen, catalogue scan and grouping regression.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {pathToFileURL}=require('node:url'),{execFileSync}=require('node:child_process');
(async()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'pbi-catalog-'));
 const report=path.join(root,'Sales #1.html');
 fs.copyFileSync('/tmp/pbidocgen-browser.html',report);
 execFileSync('python',['generate_docs.py','--catalog',root]);
 const browser=await chromium.launch({headless:true});
 const context=await browser.newContext({acceptDownloads:true,viewport:{width:1400,height:1000}});
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const download=async(label,dest)=>{const event=page.waitForEvent('download');await page.getByRole('button',{name:label,exact:true}).click();await (await event).saveAs(dest);};
 try{
  await page.goto(pathToFileURL(report).href);
  await page.locator('#sec-details').click();
  assert.match(await page.locator('#doc-status').innerText(),/No unsaved changes/);
  await page.locator('#doc-location').fill('C:\\Reports\\Finance\\Monthly\\Sales.pbip');
  await page.locator('#doc-0-username').fill('CORP\\reader');
  assert.match(await page.locator('#doc-status').innerText(),/2 unsaved changes/);
  await page.locator('#sec-overview').click();await page.locator('#sec-details').click();
  assert.equal(await page.locator('#doc-0-username').inputValue(),'CORP\\reader');
  await page.getByRole('button',{name:'Add connection reference',exact:true}).click();
  const last=await page.locator('input[id$="-username"]').count()-1;
  await page.locator(`#doc-${last}-username`).fill('second_user');
  await page.locator('#doc-edit-0-btn').click();
  await page.locator('#doc-edit-0').getByRole('button',{name:'Remove reference',exact:true}).click();
  assert.equal(await page.locator('input[id$="-username"]').last().inputValue(),'second_user');
  await page.locator('#doc-folder').fill('Finance / <img src=x onerror="globalThis.injected=1">');
  await download('Download updated HTML',report);
  await page.goto(pathToFileURL(report).href);await page.locator('#sec-details').click();
  assert.equal(await page.locator('input[id$="-username"]').filter({}).evaluateAll(ns=>ns.some(n=>n.value==='second_user')),true);
  await page.locator('#doc-folder').fill('Finance / Monthly');
  await download('Download updated HTML',report);
  execFileSync('python',['generate_docs.py','--catalog',root]);
  await page.goto(pathToFileURL(path.join(root,'pbi-home.html')).href);
  await page.getByText('Monthly',{exact:true}).waitFor();
  assert.match(await page.locator('#tree').textContent(),/second_user/); // inside collapsed card details
  // Cross-report source index and clickable source tags.
  // The hub splits into Reports / Sources / Manage views.
  await page.locator('a[data-view="sources"]').click();
  assert.match(await page.locator('#source-index').innerText(),/SQL Server · server \/ db/);
  await page.locator('#source-index .tag').first().click();
  assert.equal(await page.locator('#search').inputValue(),await page.locator('#source-index .tag').first().innerText());
  assert.equal(await page.locator('.card').count(),1);await page.locator('#search').fill('');
  assert.equal(await page.locator('.card h3 a').getAttribute('href'),'Sales%20%231.html');
  assert.equal(await page.evaluate(()=>locationLink('javascript:alert(1)')),'');
  assert.equal(await page.evaluate(()=>locationLink('https://user:secret@example.com/report')),'');
  fs.writeFileSync(path.join(root,'legacy.html'),'<script>const DATA = {"title":"Legacy report"};</script>');
  fs.mkdirSync(path.join(root,'nested'));fs.writeFileSync(path.join(root,'nested','ignored.html'),'ignored');
  await page.locator('#folder-picker').setInputFiles(root);
  await page.getByText('Legacy report',{exact:true}).waitFor();
  assert.equal(await page.locator('.card').count(),2);
  assert.ok((await page.locator('.card h3 a').first().getAttribute('href')).startsWith('blob:'));
  await page.locator('#search').fill('second_user');assert.equal(await page.locator('.card').count(),1);
  await page.locator('#search').fill('');
  await page.locator('a[data-view="manage"]').click();
  await download('Download refreshed home',path.join(root,'pbi-home.html'));
  await page.goto(pathToFileURL(path.join(root,'pbi-home.html')).href);
  assert.equal(await page.locator('.card').count(),2);
  assert.ok(!(await page.locator('.card h3 a').first().getAttribute('href')).startsWith('blob:'));
  await page.screenshot({path:'/tmp/pbi-catalog-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  await page.screenshot({path:'/tmp/pbi-catalog-mobile.png',fullPage:true});
  assert.deepEqual(errors,[]);console.log('Offline metadata save/reopen, remove/edit, catalogue scan/save, grouping, URL checks and mobile layout passed.');
 }finally{await browser.close();fs.rmSync(root,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1)});
