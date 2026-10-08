// The Model view relationship surface in real Chromium (UI rework, Change 7).
// Run through check_browser.py: node browser_relationships.cjs <pbidocgen-browser.html>
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const {surfaceProblems}=require('./view_geometry.cjs');
const step=name=>console.log('  ok '+name);
(async()=>{
 const browser=await chromium.launch(process.env.CHROMIUM_PATH?{headless:true,executablePath:process.env.CHROMIUM_PATH}:{headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:900}});
 const page=await context.newPage();const errors=[],requests=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
 page.on('request',r=>{if(!r.url().startsWith('file:')&&!r.url().startsWith('data:')&&!r.url().startsWith('blob:'))requests.push(r.url());});
 const url=pathToFileURL(path.resolve(process.argv[2])).href;
 const box=name=>page.locator('#rel-canvas .rel-box').filter({has:page.locator('title',{hasText:new RegExp('^'+name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+', ')})});
 const opacity=locator=>locator.evaluate(el=>+getComputedStyle(el).opacity);
 const panel=async()=>(await page.locator('#rel-panel').innerText()).replace(/\s+/g,' ').trim();
 try{
  await page.goto(url);
  // More relationships than the fixture file has, so every kind of line is on the surface: one filtering both
  // ways, one whose direction the file leaves to Power BI, and one to the automatic date table (never drawn).
  await page.evaluate(()=>{
   M.relationships.push({fromTable:'Sales',fromColumn:'Key',toTable:'LocalDateTable_1f',toColumn:'Date',fromCardinality:'many',toCardinality:'one',isActive:true,crossFilteringBehavior:'oneDirection'},{fromTable:'Budget',fromColumn:'Budgeted',toTable:'Dim',toColumn:'ID',fromCardinality:'many',toCardinality:'one',isActive:true,crossFilteringBehavior:'bothDirections'},
    {fromTable:'Lake',fromColumn:'Qty',toTable:'Dim',toColumn:'ID',fromCardinality:'many',toCardinality:'many',isActive:true,crossFilteringBehavior:'automatic'});
   switchTab('rels',true,true);});
  // ---- one surface: diagram and panel side by side in one card; the list is another tab
  assert.equal(await page.locator('#sec-model').getAttribute('aria-current'),'true');
  assert.deepEqual((await page.locator('.subtabs .subtab').allInnerTexts()).slice(0,2).map(t=>t.replace(/\s*\d+$/,'')),['Relationships','Relationships list']);
  const frame=async id=>page.locator(id).boundingBox();
  let diagram=await frame('#rel-viewport'),side=await frame('#rel-panel'),card=await frame('#rel-surface');
  assert.ok(side.x>=diagram.x+diagram.width-1,'the panel is beside the diagram');
  assert.ok(side.y<diagram.y+diagram.height&&diagram.y<side.y+side.height,'and level with it');
  assert.ok(side.x+side.width<=card.x+card.width+1&&diagram.x>=card.x-1,'both are inside one card');
  assert.equal(await page.locator('#main table').count(),0,'no detail table under the diagram');
  assert.equal(await page.locator('#rel-canvas svg').count(),1);
  assert.equal(await page.locator('#rel-canvas svg image, #rel-canvas svg foreignObject, #rel-canvas canvas').count(),0,'plain SVG shapes only');
  const tables=await page.evaluate(()=>countedTables().map(t=>t.name));
  assert.equal(await page.locator('#rel-canvas .rel-box').count(),tables.length,'every counted table is a box');
  assert.equal(await page.locator('#rel-canvas .rel-edge').count(),4,'every relationship is a line');
  assert.equal(await page.locator('#rel-canvas .rel-box',{has:page.locator('title',{hasText:/^LocalDateTable_1f,/})}).count(),0,'automatic date tables are not drawn');
  assert.match(await panel(),/^This model 13 tables · 4 relationships · 1 inactive · 1 filtering both ways Select a table/);
  assert.deepEqual(await surfaceProblems(page),[]);
  await page.evaluate(()=>switchTab('rels',true,true));
  step('one surface: tables as boxes, relationships as lines, the panel beside the diagram');
  // ---- each line carries cardinality and the cross-filter direction; the kinds are visibly distinct
  const lines=await page.locator('#rel-canvas .rel-edge').evaluateAll(gs=>gs.map(g=>{const line=g.querySelector('.rel-line'),arrow=g.querySelector('path.rel-arrow'),style=getComputedStyle(line);
   return {cls:g.getAttribute('class'),dash:style.strokeDasharray,colour:style.stroke,width:parseFloat(style.strokeWidth),cards:[...g.querySelectorAll('.rel-card')].map(t=>t.textContent),
    heads:arrow?(arrow.getAttribute('d').match(/Z/g)||[]).length:0,open:!!g.querySelector('circle.rel-open'),length:line.getTotalLength(),
    arrowBox:(arrow||g.querySelector('circle')).getBoundingClientRect().width};}));
  assert.deepEqual(lines.map(l=>l.cards),[['*','1'],['*','1'],['*','1'],['*','*']]);
  assert.deepEqual(lines.map(l=>l.dash!=='none'),[true,false,false,false],'the inactive relationship, and only it, is dashed');
  assert.deepEqual(lines.map(l=>l.heads),[1,1,2,0],'one arrowhead for single direction, two for both');
  assert.deepEqual(lines.map(l=>l.open),[false,false,false,true],'a hollow dot when the direction is left to Power BI');
  assert.notEqual(lines[2].colour,lines[1].colour,'both-direction filtering also has its own colour');
  for(const l of lines){assert.ok(l.length>20,'the line has length');assert.ok(l.arrowBox>4,'the direction mark is painted');assert.ok(l.width>=1.5);}
  // the cardinality marks are painted where they can be read: inside the picture, not under a box
  const hidden=await page.evaluate(()=>[...document.querySelectorAll('#rel-canvas .rel-card')].filter(t=>{const r=t.getBoundingClientRect();
   return [...document.querySelectorAll('#rel-canvas .rel-box rect')].some(b=>{const q=b.getBoundingClientRect();return r.left+r.width/2>q.left&&r.left+r.width/2<q.right&&r.top+r.height/2>q.top&&r.top+r.height/2<q.bottom;});}).length);
  assert.equal(hidden,0);
  assert.equal((await page.locator('#rel-legend').innerText()).replace(/\s+/g,' ').trim(),'Active Inactive Filters one way Filters both ways 1 one · * many');
  step('lines carry cardinality and direction; inactive, single and both are told apart without colour');
  // ---- selecting a table: its relationships stay, the rest fades, the panel opens; nothing has to be scrolled
  await box('Dim').click();
  assert.equal(await box('Dim').getAttribute('aria-pressed'),'true');
  assert.equal(await opacity(box('Dim')),1);assert.equal(await opacity(box('Sales')),1);assert.equal(await opacity(box('Budget')),1);
  assert.ok(await opacity(box('Calendar'))<.5,'an unrelated table fades');assert.ok(await opacity(box('Sales-US'))<.5);
  const kept=await page.locator('#rel-canvas .rel-edge').evaluateAll(gs=>gs.map(g=>[+getComputedStyle(g).opacity,parseFloat(getComputedStyle(g.querySelector('.rel-line')).strokeWidth)]));
  assert.deepEqual(kept.map(([o])=>o===1),[true,false,true,true],'its three relationships stay, the fourth fades');
  assert.ok(kept[1][0]<.5);assert.ok(kept[0][1]>kept[1][1],'and are drawn heavier');
  const faded=await page.locator('#rel-edge-0 .rel-line').evaluate(el=>getComputedStyle(el).strokeDasharray);assert.notEqual(faded,'none','the inactive one is still dashed while selected');
  let text=await panel();
  assert.match(text,/^Dim Clear selection dimension defined by Power Query Dim opens its columns in Table view 3 relationships · 1 inactive · 1 filtering both ways/);
  assert.match(text,/Sales inactive Sales\[Key\] → Dim\[ID\] many to one · inactive · Single direction: Dim filters Sales/);
  assert.match(text,/Budget both ways Budget\[Budgeted\] → Dim\[ID\] many to one · active · Filters both ways/);
  assert.match(text,/Lake Lake\[Qty\] → Dim\[ID\] many to many · active · Automatic: Power BI decides the direction/);
  assert.ok(!text.includes('Calendar'),'only its own relationships are in the panel');
  assert.equal(await page.locator('#rel-focus').inputValue(),'Dim');
  const inView=async id=>{const b=await frame(id);return b.y>=0&&b.y<900&&b.x>=0&&b.x<1440;};
  assert.ok(await inView('#rel-box-'+await page.evaluate(()=>M.tables.findIndex(t=>t.name==='Dim')))&&await inView('#rel-panel h2'),'the table and its detail are on screen together');
  assert.equal(await page.evaluate(()=>window.scrollY),0,'without scrolling');
  step('selecting a table highlights its relationships, fades the rest and opens the side panel');
  // ---- from the panel: the other table, the table's own page, and back to nothing selected
  await page.locator('#rel-panel .rel-item-head button',{hasText:/^Sales$/}).click();
  assert.match(await panel(),/^Sales Clear selection fact /);
  assert.equal(await box('Sales').getAttribute('aria-pressed'),'true');assert.equal(await box('Dim').getAttribute('aria-pressed'),'false');
  assert.match(await panel(),/Calendar Sales\[Double\] fx → Calendar\[Year\] fx many to one · active/,'calculated columns keep the fx marker in the panel');
  assert.match(await panel(),/Also 1 relationship to an automatic date table, not drawn here; see the Relationships list/);
  await page.locator('#rel-clear').click();
  assert.match(await panel(),/^This model/);assert.equal(await page.locator('#rel-canvas .is-faded').count(),0);
  assert.equal(await page.locator('#rel-canvas [aria-pressed="true"]').count(),0);
  await box('Calendar').click();await box('Calendar').click();
  assert.match(await panel(),/^This model/,'the same table again clears the selection');
  await box('Calendar').click();
  await page.locator('#rel-viewport').click({position:{x:6,y:6}});
  assert.match(await panel(),/^This model/,'so does a click on the empty canvas');
  step('the panel leads to the related table; the selection clears from the panel, the box or the canvas');
  // ---- keyboard: boxes are buttons; Enter and Space select and focus stays; Escape clears
  await box('Sales').focus();
  await page.keyboard.press('Enter');
  assert.equal(await box('Sales').getAttribute('aria-pressed'),'true');
  assert.equal(await page.evaluate(()=>document.activeElement?.dataset?.table),'Sales','focus stays on the box after it is selected');
  await page.keyboard.press('Tab');
  assert.ok(await page.evaluate(()=>!!document.activeElement.closest('#rel-canvas .rel-box')),'Tab moves on to the next table');
  await page.keyboard.press(' ');
  const second=await page.evaluate(()=>document.activeElement.dataset.table);
  assert.match(await panel(),new RegExp('^'+second.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+' Clear selection'));
  await page.keyboard.press('Escape');
  assert.match(await panel(),/^This model/);
  assert.equal(await page.evaluate(()=>document.activeElement.id),'rel-viewport');
  await page.locator('#rel-focus').selectOption('Budget');
  assert.equal(await box('Budget').getAttribute('aria-pressed'),'true');assert.match(await panel(),/^Budget Clear selection/);
  await page.locator('#rel-focus').selectOption('');
  assert.match(await panel(),/^This model/);
  // a table name is data: selecting the awkwardly named table runs nothing
  await box("x');globalThis.reviewMarker=1;//").click();
  assert.equal(await page.evaluate(()=>globalThis.reviewMarker),undefined);
  assert.match(await panel(),/^x'\);globalThis\.reviewMarker=1;\/\/ Clear selection .* No relationships This table is related to no other table/);
  await page.locator('#rel-clear').click();
  step('boxes work from the keyboard and the table picker; a table name never runs as script');
  // ---- a line can be selected too
  await page.locator('#rel-edge-2 .rel-hit').click({force:true});
  text=await panel();
  assert.match(text,/^Relationship Clear selection Budget → Dim both ways Budget\[Budgeted\] → Dim\[ID\] many to one · active · Filters both ways From Budget many side To Dim one side Status Active Cross-filter Filters both ways/);
  assert.equal(await opacity(page.locator('#rel-edge-2')),1);assert.ok(await opacity(page.locator('#rel-edge-0'))<.5);
  assert.ok(await opacity(box('Sales'))<.5);assert.equal(await opacity(box('Budget')),1);
  await page.keyboard.press('Escape');
  // zoom: the picture scales, the selection survives, Fit shows all of it
  const width=()=>page.locator('#rel-canvas svg').evaluate(s=>s.getBoundingClientRect().width);
  const before=await width();
  await page.getByRole('button',{name:'Zoom in',exact:true}).click();assert.ok(await width()>before);
  await page.getByRole('button',{name:'Zoom out',exact:true}).click();await page.getByRole('button',{name:'Zoom out',exact:true}).click();assert.ok(await width()<before);
  await page.getByRole('button',{name:'Fit',exact:true}).click();
  assert.ok(await width()<=(await frame('#rel-viewport')).width,'Fit shows the whole diagram');
  step('a line selects its relationship; zoom and Fit scale the picture');
  // ---- the relationships list is its own tab, complete, and leads back to the diagram
  await page.locator('#nav-rel-list').click();
  await page.waitForFunction(()=>activeTab==='rel-list');
  assert.match(await page.locator('#main h1').innerText(),/^Relationships list$/);
  assert.equal(await page.locator('#rel-surface').count(),0);
  assert.equal(await page.locator('#rel-list-rows tr').count(),4);
  assert.equal(await page.locator('#rel-list-count').innerText(),'4 relationships');
  assert.deepEqual(await page.locator('#rel-list-rows tr').evaluateAll(rs=>rs.map(r=>[...r.cells].slice(3,5).map(c=>c.textContent.trim()).join('/'))),
   ['inactive/single','active/single','active/both','active/automatic']);
  await page.locator('#rel-list-search').fill('budget');
  assert.equal(await page.locator('#rel-list-rows tr').count(),1);assert.equal(await page.locator('#rel-list-count').innerText(),'1 of 4 relationships');
  await page.locator('#rel-list-search').fill('nothing like this');
  assert.match(await page.locator('#rel-list-rows').innerText(),/No relationships match this search\./);
  await page.locator('#rel-list-search').fill('');
  assert.equal(await page.locator('#rel-auto').evaluate(el=>el.open),false,'relationships to automatic date tables are apart, closed');
  assert.equal(await page.locator('#rel-auto tbody tr').count(),1);
  await page.locator('#rel-list-rows tr').nth(2).getByRole('button',{name:/^Show Budget to Dim on the diagram$/}).click();
  await page.waitForFunction(()=>activeTab==='rels');
  assert.match(await panel(),/^Relationship Clear selection Budget → Dim /);
  assert.equal(await opacity(page.locator('#rel-edge-2')),1);assert.ok(await opacity(page.locator('#rel-edge-1'))<.5);
  await page.goBack();await page.waitForFunction(()=>activeTab==='rel-list');
  await page.goForward();await page.waitForFunction(()=>activeTab==='rels');
  await page.keyboard.press('Escape');await page.locator('#rel-clear').click().catch(()=>{});
  // the Overview number opens the list and equals its rows
  await page.evaluate(()=>switchTab('overview'));
  const counted=Number(await page.locator('#count-relationships .big').innerText());
  await page.locator('#count-relationships').click();
  await page.waitForFunction(()=>activeTab==='rel-list');
  assert.equal(await page.locator('#rel-list-rows tr').count(),counted);
  // a table link in the list still opens the table
  await page.locator('#rel-list-rows tr').first().locator('button.xl',{hasText:/^Sales$/}).click();
  await page.waitForFunction(()=>activeTab==='tables');
  step('the relationships list is its own tab, searchable, and each row opens on the diagram');
  // ---- a wide model scrolls rather than shrinking past reading; a phone stacks the panel under the diagram
  await page.evaluate(()=>{for(let i=0;i<4;i++){M.tables.push({name:'Chain '+i,tableType:'dimension',columns:[],measures:[],partitions:[]});
    M.relationships.push({fromTable:i?'Chain '+(i-1):'Calendar',fromColumn:'k',toTable:'Chain '+i,toColumn:'k',fromCardinality:'many',toCardinality:'one',isActive:true,crossFilteringBehavior:'oneDirection'});}
   relZoom=0;switchTab('rels',true,true);});
  const zoom=parseInt(await page.locator('#rel-zoom-label').innerText(),10);
  assert.ok(zoom>=75,'names stay readable: '+zoom+'%');
  assert.ok(await page.locator('#rel-viewport').evaluate(v=>v.scrollWidth>v.clientWidth),'the diagram scrolls inside its frame');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth),'the page itself does not');
  const fact=await frame('#rel-box-'+await page.evaluate(()=>M.tables.findIndex(t=>t.name==='Sales'))),view=await frame('#rel-viewport');
  assert.ok(fact.x>=view.x&&fact.x+fact.width<=view.x+view.width,'it opens on the fact table');
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(()=>{relZoom=0;switchTab('rels',true,true);selectRelTable('Sales');});
  diagram=await frame('#rel-viewport');side=await frame('#rel-panel');
  assert.ok(side.y>=diagram.y+diagram.height-1,'on a phone the panel is under the diagram');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth),'no sideways page scroll on a phone');
  assert.match(await panel(),/^Sales Clear selection/);
  step('a wide model scrolls inside its frame and opens on the fact table; a phone stacks the panel');
  assert.deepEqual(requests,[],'nothing is fetched: no library, font or image');
  assert.deepEqual(errors,[]);
  console.log('Relationship surface checks passed in Chromium.');
 }catch(error){
  console.error(error);if(errors.length)console.error('page errors:',errors);
  await page.screenshot({path:path.join(require('node:os').tmpdir(),'browser-relationships-failure.png')}).catch(()=>{});
  process.exitCode=1;
 }finally{await browser.close();}
})();
