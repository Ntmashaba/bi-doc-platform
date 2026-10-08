// Table kinds, the fx marker and the calculation tabs in real Chromium (UI rework, Change 5).
// Run through check_browser.py: node browser_model_kinds.cjs <pbidocgen-browser.html>
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
 const url=pathToFileURL(path.resolve(process.argv[2])).href;
 const card=name=>page.locator('#tbl-list > details').filter({has:page.locator(`xpath=./summary[starts-with(normalize-space(.), "${name} ")]`)});
 try{
  await page.goto(url);
  // ---- every table says, once, how it is defined; its role label is as before
  await page.evaluate(()=>switchTab('tables'));
  const expected={Sales:['fact','Power Query'],Dim:['dimension','Power Query'],Calendar:['date dimension','Calculated table'],
   'LocalDateTable_1f':['date dimension','Automatic date table'],'LocalDateTable_lookalike':['disconnected','Calculated table'],
   'Time Intelligence':['calculation group','Calculation group'],Budget:['disconnected','SQL query'],Lake:['disconnected','Entity'],
   'Sales-US':['disconnected','Other (no partition)']};
  const shown=await page.locator('#tbl-list > details').evaluateAll(cards=>Object.fromEntries(cards.map(c=>[c.dataset.table,
   [c.querySelector(':scope > summary > .badge').textContent,[...c.querySelectorAll(':scope > summary > .defined')].map(d=>d.textContent.replace(/^defined by\s*/,''))]])));
  for(const [name,[role,defined]] of Object.entries(expected)) assert.deepEqual(shown[name],[role,[defined]],name);
  assert.ok(Object.values(shown).every(([,defined])=>defined.length===1),'exactly one kind per table');
  assert.ok(await page.evaluate(()=>M.tables.every(t=>DEFINED_KINDS.includes(definedBy(t).kind))),'every kind is one of the seven');
  // An automatic date table is one only when the file marks it: the same name without the marker is a calculated table.
  assert.equal(shown['LocalDateTable_lookalike'][1][0],'Calculated table');
  await page.locator('#tbl-'+await page.evaluate(()=>slug('Calendar'))+' > summary').click();
  const calendar=page.locator('#tbl-'+await page.evaluate(()=>slug('Calendar')));
  assert.match(await calendar.locator('.defined-line').innerText(),/^Defined by Calculated table\. Its rows come from a DAX expression/);
  // Columns are classified one by one: in a calculated table, Year is a calculated column and Date is not.
  const rows=await calendar.locator('tr[data-column]').evaluateAll(trs=>Object.fromEntries(trs.map(tr=>[tr.dataset.column,
   {fx:!!tr.querySelector('.fx'),fromTable:/from the table expression/.test(tr.cells[0].textContent)}])));
  assert.deepEqual(rows,{Date:{fx:false,fromTable:true},Year:{fx:true,fromTable:false}});
  await page.locator('#tbl-'+await page.evaluate(()=>slug('Sales'))+' > summary').click();
  const sales=page.locator('#tbl-'+await page.evaluate(()=>slug('Sales')));
  assert.match(await sales.locator('.defined-line').innerText(),/^Defined by Power Query\. Query: Sales in Power Query\./);
  assert.deepEqual(await sales.locator('tr[data-column]').evaluateAll(trs=>trs.filter(tr=>tr.querySelector('.fx')).map(tr=>tr.dataset.column)),['Double']);
  step('each table shows one "defined by" kind beside its unchanged role; columns are classified one by one');
  // ---- links from a table to its definition, and back
  await sales.locator('.defined-line button',{hasText:'Sales'}).click();
  await page.waitForFunction(()=>activeTab==='power-query'&&document.getElementById('pq-title')?.textContent==='Sales');
  await page.goBack();
  await page.waitForFunction(()=>activeTab==='tables');
  await page.locator('#tbl-'+await page.evaluate(()=>slug('Calendar'))+' .defined-line button').click();
  await page.waitForFunction(()=>activeTab==='calc-tables');
  const calendarCard=page.locator('#'+await page.evaluate(()=>calcTableAnchor('Calendar')));
  await page.waitForFunction(id=>document.getElementById(id)?.classList.contains('obj-hit'),await page.evaluate(()=>calcTableAnchor('Calendar')));
  assert.match(await calendarCard.locator('pre').innerText(),/^CALENDAR\(DATE\(2024, 1, 1\)/);
  step('a table links to its query, its DAX expression or its calculation items');
  // ---- DAX query view tabs: each lists exactly the objects of its kind, and its count says how many
  const tabCount=async id=>Number((await page.locator('#nav-'+id+' .n').innerText().catch(()=>'0')).trim());
  await page.evaluate(()=>switchTab('calc-columns'));
  assert.deepEqual(await page.locator('.subtab').filter({hasText:/^(Measures|Calculated|Calculation)/}).allInnerTexts().then(a=>a.map(t=>t.replace(/\s*\d+$/,''))),
   ['Measures','Calculated Columns','Calculated Tables','Calculation Groups']);
  const columnCards=page.locator('#calc-column-list details.calc-col');
  assert.deepEqual(await columnCards.locator('.mea-name').allInnerTexts(),['Double fx','Year fx']);
  assert.equal(await tabCount('calc-columns'),2);
  assert.equal(await columnCards.count(),await page.evaluate(()=>M.tables.reduce((n,t)=>n+t.columns.filter(c=>c.isCalculated).length,0)));
  await columnCards.first().locator('summary').click();
  assert.equal(await columnCards.first().locator('pre').innerText(),'[Amount] * 2');
  assert.match((await columnCards.first().locator('tr',{hasText:'Depends on'}).innerText()).replace(/\s+/g,' '),/Sales\[Amount\]/);
  await page.locator('#calc-column-search').fill('year(');
  assert.deepEqual(await page.locator('#calc-column-list details.calc-col:visible .mea-name').allInnerTexts(),['Year fx']);
  assert.equal(await page.locator('#calc-column-status').innerText(),'1 calculated column shown');
  await page.locator('#calc-column-search').fill('zz');
  assert.equal(await page.locator('#calc-column-status').innerText(),'No matching calculated columns.');
  await page.locator('#calc-column-search').fill('');
  // from the tab to the column in Table view, and from the column's fx back to its expression
  await columnCards.first().locator('button',{hasText:'Open Double in Table view'}).click();
  const row=await page.evaluate(()=>'#'+columnAnchor('Sales','Double'));
  await page.waitForFunction(s=>document.querySelector(s)?.classList.contains('obj-hit'),row);
  assert.equal(await page.evaluate(()=>activeTab),'tables');
  await page.locator(row+' .fx-link').click();
  await page.waitForFunction(()=>activeTab==='calc-columns');
  await page.waitForFunction(id=>document.getElementById(id)?.classList.contains('obj-hit'),await page.evaluate(()=>calcColumnAnchor('Sales','Double')));
  assert.ok(await page.locator('#'+await page.evaluate(()=>calcColumnAnchor('Sales','Double'))).evaluate(el=>el.open));
  await page.evaluate(()=>switchTab('calc-tables'));
  assert.deepEqual(await page.locator('details.calc-table .mea-name').allInnerTexts(),['Calendar','LocalDateTable_lookalike']);
  assert.equal(await tabCount('calc-tables'),2);
  assert.match(await page.locator('#main').innerText(),/1 automatic date table that Power BI creates for date columns is not listed here/);
  assert.match((await page.locator('details.calc-table').first().locator('tr',{hasText:'Calculated columns added'}).innerText()).replace(/\s+/g,' '),/Year fx/);
  await page.evaluate(()=>switchTab('calc-groups'));
  assert.deepEqual(await page.locator('details.calc-group .mea-name').allInnerTexts(),['Time Intelligence']);
  assert.equal(await tabCount('calc-groups'),1);
  assert.deepEqual(await page.locator('details.calc-group .calc-item').allInnerTexts(),['YTD','PY'],'items in their ordinal order');
  assert.match(await page.locator('details.calc-group').innerText(),/precedence 10/);
  assert.match(await page.locator('details.calc-group').innerText(),/Format string expression/);
  step('Measures, Calculated Columns, Calculated Tables and Calculation Groups each list their own objects, with matching counts');
  // ---- the finder tags every object with its kind
  const kindOf=name=>page.evaluate(n=>OBJECTS.filter(o=>o.name===n&&!o.parent).map(o=>o.kind),name);
  assert.deepEqual(await kindOf('Calendar'),['calculated table']);
  assert.deepEqual(await kindOf('LocalDateTable_1f'),['automatic date table']);
  assert.deepEqual(await kindOf('LocalDateTable_lookalike'),['calculated table']);
  assert.deepEqual(await kindOf('Time Intelligence'),['calculation group']);
  assert.deepEqual(await kindOf('Budget'),['table']);
  assert.deepEqual(await page.evaluate(()=>OBJECTS.filter(o=>o.kind==='calculated column').map(o=>o.parent+'['+o.name+']')),['Sales[Double]','Calendar[Year]']);
  assert.equal(await page.evaluate(()=>OBJECTS.filter(o=>o.name==='Date'&&o.parent==='Calendar')[0].kind),'column');
  await page.locator('#finder-input').fill('Year');
  assert.equal(await page.locator('#finder-list [role=option][aria-label="Year, calculated column, Calendar"] .fx').count(),1);
  await page.locator('#finder-clear').click();
  step('the search index tags tables and columns with their kind');
  // ---- fx wherever a calculated column is named
  // Every element whose own text names Sales[Double] or its column, in every view, carries the marker beside it.
  const unmarked=()=>page.evaluate(()=>{
   const missing=[];
   const walker=document.createTreeWalker(document.querySelector('#main'),NodeFilter.SHOW_TEXT);
   for(let node=walker.nextNode();node;node=walker.nextNode()){
    if(!/\bDouble\b/.test(node.nodeValue)) continue;
    const el=node.parentElement;
    if(el.closest('pre,.mea-preview,option,select,title,#table-search-status,.tbl-match,.pq-detail,script')) continue;   // code, or a place markup cannot go
    if(/Open Double in Table view|Doubled/.test(node.nodeValue)&&!/\bDouble\b(?!d)/.test(node.nodeValue.replace(/Open Double in Table view/,''))) continue;
    const scope=el.closest('td,th,li,p,summary,.pill-list,button.visual-box')||el;
    const marked=scope.querySelector('.fx')||/\(fx\)/.test(scope.textContent);
    if(!marked) missing.push(activeTab+': '+scope.textContent.replace(/\s+/g,' ').trim().slice(0,90));
   }
   return missing;});
  const open=async(tab,prepare)=>{await page.evaluate(t=>{pageScope='*';switchTab(t,false,true);},tab);if(prepare)await prepare();
   const missing=await unmarked();assert.deepEqual(missing,[],'fx is missing in '+tab);
   return page.locator('#main .fx').count();};
  const counts={};
  counts.tables=await open('tables',()=>page.evaluate(()=>document.querySelectorAll('#tbl-list > details').forEach(d=>d.open=true)));
  counts.columns=await open('columns');
  counts.measures=await open('measures',()=>page.evaluate(()=>document.querySelectorAll('#mea-list details').forEach(d=>d.open=true)));
  counts.rels=await open('rels');
  counts.usage=await open('usage');
  counts.pages=await open('pages',()=>page.evaluate(()=>document.querySelectorAll('#main details').forEach(d=>d.open=true)));
  counts.filters=await open('filters');
  counts.manifest=await open('manifest');
  counts.matrix=await open('matrix',()=>page.evaluate(()=>{expandedTables.add('Sales');switchTab('matrix');}));
  counts.cleanup=await open('cleanup',()=>page.evaluate(()=>{cleanupDecision='';switchTab('cleanup');}));
  counts.layout=await open('layout');
  counts.impact=await open('impact',()=>page.evaluate(()=>{impactNode=nodeId('m','Sales','Doubled');renderImpactOptions();impactNode=nodeId('m','Sales','Doubled');renderImpactDetails();}));
  counts['calc-columns']=await open('calc-columns');
  for(const tab of ['overview','warnings','sources','primary-sources','security','lineage','power-query','calc-tables','calc-groups']) await open(tab);
  for(const [tab,n] of Object.entries(counts)) if(tab!=='layout') assert.ok(n>=1,'the marker is shown in '+tab+' ('+n+')');
  // places where markup cannot go use the same marker as text
  await page.evaluate(()=>switchTab('impact'));
  assert.ok((await page.locator('#impact-field option').allInnerTexts()).some(t=>t==='Sales[Double] (fx) · column'),'a drop-down names it with (fx)');
  await page.evaluate(()=>switchTab('layout'));
  assert.ok((await page.locator('.visual-box .vb-fields').allInnerTexts()).some(t=>/Double \(fx\)/.test(t)),'a layout box names it with (fx)');
  await page.evaluate(()=>inspectVisual('p2','v1'));
  assert.ok(await page.locator('#inspector .fx').count()>=2,'the visual inspector marks the binding and the filter');
  await page.evaluate(()=>{closeInspector();inspectNode(nodeId('c','Sales','Double'),'*');});
  assert.match(await page.locator('#inspector').innerText(),/^Close\s*Sales\[Double\]\s*calculated column · Sales\[Double\] fx/);
  await page.evaluate(()=>closeInspector());
  // a column that is not calculated never carries it
  await page.evaluate(()=>{pageScope='*';switchTab('tables',false,true);document.querySelectorAll('#tbl-list > details').forEach(d=>d.open=true);});
  assert.equal(await page.locator(await page.evaluate(()=>'#'+columnAnchor('Sales','Amount'))+' .fx').count(),0);
  step('fx marks a calculated column in every view that names one, as text where markup cannot go');
  // ---- narrow screens
  await page.setViewportSize({width:390,height:844});
  for(const tab of ['calc-columns','calc-tables','calc-groups']){
   await page.evaluate(t=>switchTab(t),tab);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),tab+' fits a phone');
  }
  step('the calculation tabs fit a phone');
  assert.deepEqual(errors,[]);
  console.log('Table kind, fx and calculation tab checks passed in Chromium.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
