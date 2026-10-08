// Overview numbers are promises: each is a link, and the list it opens holds that many items.
// Shared by the fixture checks (browser_sections.cjs) and the sample checks (scripts/check_sample_html.cjs).
const SHOWN_IN={
 'source-tables':()=>document.querySelectorAll('#tbl-group-source details[data-table]').length,
 'calc-tables':()=>document.querySelectorAll('details.calc-table').length,
 'calc-groups':()=>document.querySelectorAll('details.calc-group').length,
 'other-tables':()=>document.querySelectorAll('#tbl-group-other details[data-table]').length,
 'columns':()=>Number(/· ([\d,]+) distinct columns/.exec(document.getElementById('column-count').textContent)[1].replace(/,/g,'')),
 'calc-columns':()=>document.querySelectorAll('#calc-column-list details.calc-col').length,
 'measures':()=>document.querySelectorAll('#mea-list details.measure').length,
 'relationships':()=>document.querySelectorAll('#erd-rows tr').length,
 'queries':()=>document.querySelectorAll('.pq-item').length,
 'sources':()=>document.querySelectorAll('#source-list-rows tr').length,
 'roles':()=>document.querySelectorAll('#main [id^="role-"]').length,
 'pages':()=>document.querySelectorAll('#main details[id^="pg-"]').length,
 'visuals':()=>document.querySelectorAll('#main [id^="vis-"]').length,
 'filters':()=>document.querySelectorAll('#main table.t tbody tr').length,
 'bookmarks':()=>document.querySelectorAll('#bookmark-list tbody tr').length,
 'warnings':()=>document.querySelectorAll('#main table.t tbody tr').length};
const OPENS={'source-tables':'tables','calc-tables':'calc-tables','calc-groups':'calc-groups','other-tables':'tables',columns:'columns',
 'calc-columns':'calc-columns',measures:'measures',relationships:'rels',queries:'power-query',sources:'sources',roles:'security',
 pages:'pages',visuals:'pages',filters:'filters',bookmarks:'bookmarks',warnings:'warnings'};
// Returns the ids checked. `before` runs ahead of each count (for example to select a report page).
async function checkOverviewCounts(page,{before}={}){
 await page.evaluate(()=>switchTab('overview'));
 const ids=await page.locator('.stat-link').evaluateAll(els=>els.map(el=>el.id.replace('count-','')));
 for(const id of ids){
  if(!(id in OPENS)) throw Error(`Overview count "${id}" has no list to compare with`);
  if(before) await before();
  await page.evaluate(()=>switchTab('overview'));
  const link=page.locator('#count-'+id);
  const n=Number((await link.locator('.big').innerText()).replace(/,/g,''));
  if(await link.evaluate(el=>el.tagName)!=='BUTTON') throw Error(`Overview count "${id}" is not a link`);
  await link.click();
  await page.waitForFunction(tab=>activeTab===tab,OPENS[id]);
  await page.waitForTimeout(30);
  const listed=await page.evaluate(`(${SHOWN_IN[id].toString()})()`);
  if(listed!==n) throw Error(`Overview count "${id}" says ${n} and opens a list of ${listed}`);
 }
 await page.evaluate(()=>switchTab('overview'));
 return ids;
}
module.exports={checkOverviewCounts,OPENS};
