const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[2],'utf8'),script=html.match(/<script>([\s\S]*)<\/script>/)[1];
const nodes=new Map();const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',innerHTML:'',textContent:''});return nodes.get(id)};
const ctx=vm.createContext({console,URL,Map,document:{getElementById:node}}),run=s=>vm.runInContext(s,ctx);
run(script);
assert.equal(run("JSON.stringify(groupParts({reportLocation:'C:\\\\Reports\\\\Finance\\\\Sales.pbip'}))"),'["Finance"]');
assert.equal(run("JSON.stringify(groupParts({reportLocation:'https://example.com/teams/Finance/Sales'}))"),'["Finance"]');
assert.equal(run("JSON.stringify(groupParts({folder:'Finance / Monthly',reportLocation:'https://example.com/elsewhere'}))"),'["Finance","Monthly"]');
assert.equal(run("JSON.stringify(groupParts({reportLocation:'https://example.com/Sales'}))"),'["example.com"]');
assert.equal(run("JSON.stringify(groupParts({reportLocation:'https://t.sharepoint.com/sites/BI/Shared%20Documents/Sales.pbix'}))"),'["Shared Documents"]');
assert.equal(run("JSON.stringify(groupParts({}))"),'["Ungrouped"]');
assert.equal(run("locationLink('javascript:alert(1)')"),'');
assert.equal(run("locationLink('https://user:password@example.com/')"),'');
assert.equal(run("locationLink('C:\\\\Reports\\\\Sales #1.pbip')"),'file:///C:/Reports/Sales%20%231.pbip');
run(`entries=[{title:'<img src=x onerror=alert(1)>',filename:'x.html',href:'x.html',metadata:{folder:'<b>Finance</b>',connections:[{username:'reader'}]}}];renderCatalog()`);
assert.ok(node('tree').innerHTML.includes('&lt;img'));
assert.ok(!node('tree').innerHTML.includes('<img'));
assert.ok(node('tree').innerHTML.includes('&lt;b&gt;Finance'));
node('search').value='reader';run('renderCatalog()');assert.match(node('status').textContent,/1 of 1/);
node('search').value='unmatched';run('renderCatalog()');assert.match(node('status').textContent,/0 of 1/);
console.log('Catalogue grouping, links, escaping and username search passed.');

// Library routes reveal one workspace view, and report links stay in this tab.
const navLinks=['reports','sources','manage'].map(view=>({dataset:{view},setAttribute(){},removeAttribute(){}}));
ctx.window={location:{hash:'#sources'}};ctx.document.querySelectorAll=()=>navLinks;
run('libraryRoute()');assert.equal(node('reports-view').hidden,true);assert.equal(node('sources-view').hidden,false);
ctx.window.location.hash='#manage';run('libraryRoute()');assert.equal(node('manage-view').hidden,false);assert.equal(node('sources-view').hidden,true);
ctx.window.location.hash='#reports';run('libraryRoute()');assert.equal(node('reports-view').hidden,false);
assert.doesNotMatch(run("card({title:'Report',filename:'report.html',href:'report.html',metadata:{}})"),/target="_blank"/);
