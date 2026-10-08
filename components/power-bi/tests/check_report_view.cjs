// The Report view in the actual generated script, with a minimal DOM adapter (UI rework, Change 4: Report view).
// Run by test_page_types.py on a report read from PBIR. Layout, selection and the panel: browser_report_view.cjs.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[2],'utf8');
const nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{id,value:'',innerHTML:'',textContent:'',hidden:false,style:{},
  classList:{add(){},remove(){},toggle(){},contains:()=>false},setAttribute(k,v){this['@'+k]=v;},removeAttribute(){},getAttribute(){return null;},
  focus(){},scrollIntoView(){},contains:()=>false,querySelector:()=>null});return nodes.get(id);};
const browserWindow={scrollTo(){},location:{hash:''},addEventListener(){},history:{pushState(){},replaceState(){}}};
const context=vm.createContext({console,setTimeout:()=>0,clearTimeout(){},document:{getElementById:node,querySelectorAll:()=>[],
  querySelector:()=>null,createElement:()=>({}),body:{appendChild(){}}},window:browserWindow});
vm.runInContext(html.match(/<script>([\s\S]*)<\/script>/)[1],context);
const run=s=>vm.runInContext(s,context);
const same=(a,b)=>assert.equal(JSON.stringify(a),JSON.stringify(b));
const text=s=>s.replace(/<[^>]+>/g,' ').replace(/&quot;/g,'"').replace(/&amp;/g,'&').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/\s+/g,' ').trim();
const show=tab=>{run(`switchTab(${JSON.stringify(tab)})`);return node('main').innerHTML;};
const page=id=>{run(`showReportPage(${JSON.stringify(id)})`);assert.equal(run('activeTab'),'pages');return node('main').innerHTML;};
const strip=main=>[...main.matchAll(/<button type="button" class="page-tab[^"]*" data-page="([^"]+)"([^>]*)>([\s\S]*?)<\/button>/g)].map(m=>({id:m[1],current:/aria-current="page"/.test(m[2]),text:text(m[3])}));
const section=main=>main.slice(main.indexOf('<section class="page-view"'));
const panel=main=>text(main.slice(main.indexOf('id="page-panel"'),main.indexOf('</aside>')));

// ---- Report view: pages, every visual, the flat filter list, the field manifest, bookmarks. Page layout is in Pages.
same(run("SECTIONS.find(s=>s.id==='report').tabs"),['pages','visuals','filters','manifest','bookmarks']);
same(run("['pages','visuals','filters','manifest','bookmarks'].map(id=>TABS.find(t=>t.id===id).label)"),['Pages','Visuals','Filters','Field manifest','Bookmarks']);
assert.ok(!run("TABS.some(t=>t.id==='layout')"));assert.equal(run('TAB_ALIASES.layout'),'pages');
run("switchTab('layout')");assert.equal(run('activeTab'),'pages','an old link to Page layout opens Pages');

// ---- the page list: every page, in report order, saying which are hidden, tooltip or drillthrough pages
let main=show('pages');
same(strip(main).map(s=>s.text),['1 Home','2 Sales tooltip tooltip page · hidden','3 Order details drillthrough page',
  '4 Bound only drillthrough page','5 Default binding','6 Odd page type not recognised']);
same(strip(main).map(s=>s.current),[true,false,false,false,false,false],'the first page in report order is shown first');
assert.equal((main.match(/<section class="page-view"/g)||[]).length,1,'one page at a time');
assert.match(section(main),/^<section class="page-view" id="pg-[^"]+" data-page="home"/);
assert.match(text(section(main)),/^Home Report page · 1,280 × 720 · 0 data visuals /,'an ordinary page says so, with no badge');
assert.ok(!/class="badge pt/.test(section(main).split('</h2>')[1].split('</div>')[0]));

// a tooltip page: its type, hidden, its size and its tooltip fields
main=page('tip');
same(strip(main).filter(s=>s.current).map(s=>s.id),['tip']);
const head=main.slice(main.indexOf('<div class="page-head">'),main.indexOf('</div>',main.indexOf('<div class="page-head">')));
assert.match(head,/<h2 id="page-title" title="tip">Sales tooltip<\/h2> <span class="badge pt pt-tooltip">tooltip page<\/span> <span class="badge b-hidden">hidden<\/span>/);
assert.match(text(section(main)),/Tooltip page · 320 × 240 · 0 data visuals A tooltip page: Power BI shows it as the tooltip of visuals that use its tooltip fields: Sales \[Amount\] \. Hidden: readers of the published report do not see it in the page list\./);
// a drillthrough page: the field readers drill through on, apart from its filters
main=page('detail');
assert.match(text(section(main)),/Drillthrough page · 0 data visuals A drillthrough page: readers reach it by drilling through from another page on Dim \[ID\] \./);
assert.match(text(section(main)),/^Order details drillthrough page /);
// ...and the Filters pane: on this visual, on this page, on all pages
let p=panel(main);
assert.match(p,/Select a visual in the list below for its fields and the filters on it\. Filters /);
assert.match(p,/Filters on this visual 0 No visual on this page has a filter of its own\./);
assert.match(p,/Filters on this page 2 Region filter Dim \[Region\] basic hidden from readers locked Dim \[Name\] basic/);
assert.ok(!/Filters on this page[^]*Dim \[ID\][^]*Filters on all pages/.test(p),'a drillthrough field is not a filter in the Filters pane');
assert.match(p,/Filters on all pages 1 Sales \[Year\] basic$/);
// a binding written before page.json had a type, and an unrecognised type, as the file gives it
assert.match(text(section(page('bound'))),/^Bound only drillthrough page Drillthrough page/);
assert.match(text(section(page('odd'))),/^Odd page type not recognised Page type not recognised \(type Wallpaper\) · 0 data visuals The page settings give a page type this document does not recognise \(type Wallpaper\)\./);
assert.match(text(section(page('default'))),/^Default binding Report page · /);

// ---- a page whose type the document does not record is never shown as an ordinary page
run("delete R.pages[0].pageType");
main=page('home');
assert.equal(strip(main)[0].text,'1 Home page type not recorded');
assert.match(text(section(main)),/^Home page type not recorded Page type not recorded · 1,280 × 720 · 0 data visuals Page type not recorded: this document does not say whether it is an ordinary page, a tooltip page or a drillthrough page\./);
assert.match(section(main),/<span class="badge pt pt-unknown">page type not recorded<\/span>/);
// a document generated before page types were recorded: every page says so; hidden is still known
run("R.pages.forEach(p=>{delete p.pageType;delete p.pageTypeRaw;})");
main=show('pages');
same(strip(main).map(s=>/page type not recorded/.test(s.text)),[true,true,true,true,true,true]);
assert.match(strip(main)[1].text,/page type not recorded · hidden$/);
assert.equal(run("pageFlagsText(R.pages[1])"),' (hidden)','where markup cannot go, only what is known');

// ---- the page list follows the Report page selection of the usage views, and goes back to it on return
run("setPageScope('detail');switchTab('manifest');switchTab('pages')");
assert.equal(run('shownPage().id'),'detail');
run("setPageScope('*')");

// ---- Visuals: every visual, for searching; this report has none
show('visuals');run('filterVisualList()');
assert.equal(node('visual-count').textContent,'0 visuals');assert.match(node('visual-list-rows').innerHTML,/No visuals in this selection\./);

// ---- Filters: the flat list with its scope column, for searching
show('filters');run('filterFilterList()');
const rows=()=>node('filter-list-rows').innerHTML.split('</tr>').filter(r=>r.includes('<tr')).map(text);
const total=run('allFilters().length');
assert.equal(rows().length,total);assert.equal(node('filter-count').textContent,total+' filters');
assert.ok(rows().some(r=>/^Page drillthrough field Sales \/ Order details \[detail\] Order details Dim \[ID\]/.test(r)),'a drillthrough field is named as such');
assert.ok(rows().some(r=>/^Page .* Region filter Dim \[Region\] hidden from readers locked basic/.test(r)));
assert.ok(rows().every(r=>/^(All pages|Page|Visual) /.test(r)),'the scope is in words: all pages, page or visual');
node('filter-search').value='region';run('filterFilterList()');
assert.equal(rows().length,1);assert.equal(node('filter-count').textContent,'1 of '+total+' filters');
node('filter-search').value='zzz';run('filterFilterList()');assert.match(node('filter-list-rows').innerHTML,/No filters match this search\./);

// ---- Bookmarks: every bookmark read, in the file's order, with its group and the page it opens
main=show('bookmarks');
const marks=[...main.matchAll(/<tr><th scope="row">([\s\S]*?)<\/th><td>([\s\S]*?)<\/td>/g)].map(m=>[text(m[1]),text(m[2])]);
same(marks,[['Second','Home'],['First Group: Saved views','Order details'],['Not in the metadata','Not recorded']]);
assert.match(main,/onclick="showReportPage\(&quot;detail&quot;\)"/);
assert.equal(run("TABS.find(t=>t.id==='bookmarks').count()"),3);

// ---- Overview: the page count is the page list
assert.equal(run("OVERVIEW_COUNTS.find(c=>c.id==='pages').n()"),6);
console.log('Report view checks passed: page list, page types, Filters pane groups, flat filter list, bookmarks.');
