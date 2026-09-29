// Optional DOM integration check: npm install --no-save jsdom, then run this file.
const {JSDOM,VirtualConsole}=require('jsdom');
const fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const errors=[],windows=[];let blob,filename;
 function open(html){
  const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(html,{runScripts:'dangerously',url:'file:///docs/report.html',virtualConsole:vc,beforeParse(w){
   w.scrollTo=()=>{};w.Blob=Blob;w.URL.createObjectURL=b=>(blob=b,'blob:local');w.URL.revokeObjectURL=()=>{};
   w.HTMLAnchorElement.prototype.click=function(){filename=this.download;};
  }});windows.push(dom.window);return dom.window;
 }
 const original=fs.readFileSync('/tmp/pbidocgen-browser.html','utf8');
 let w=open(original);w.switchTab('report-details');
 function fill(id,value){w.document.getElementById(id).value=value;w.captureDocumentation();}
 fill('doc-location','C:\\Reports\\Finance\\Sales.pbip');fill('doc-0-username','CORP\\reader');
 w.switchTab('overview');w.switchTab('report-details');assert.equal(w.document.getElementById('doc-0-username').value,'CORP\\reader');
 w.addDocumentationConnection();let inputs=w.document.querySelectorAll('input[id$="-username"]');
 fill(inputs[inputs.length-1].id,'other_reader');w.removeDocumentationConnection(0);
 inputs=w.document.querySelectorAll('input[id$="-username"]');assert.equal(inputs[inputs.length-1].value,'other_reader');
 fill('doc-folder','Finance / <img src=x onerror="globalThis.injected=1">');
 w.saveDocumentationHtml();assert.equal(filename,'pbidocgen-browser.html');
 const saved=await blob.text();w=open(saved);w.switchTab('report-details');
 assert.ok([...w.document.querySelectorAll('input[id$="-username"]')].some(n=>n.value==='other_reader'));
 assert.equal(w.injected,undefined);
 let hub=open(fs.readFileSync('pbidocgen/catalog.html','utf8').replace('/*__BATCH__*/null',JSON.stringify({generated:0,failed:1,generatedAt:'Test run',files:[{title:'Sales',filename:'Sales #1.html',status:'failed',error:'Synthetic failure',previousHtmlRetained:true}]})));
 const parsed=hub.readEntry(saved,'Sales #1.html');assert.equal(parsed.metadata.connections[0].username,'other_reader');
 assert.equal(parsed.title,'Browser regression fixture');
 await hub.scanFiles([{name:'Sales #1.html',webkitRelativePath:'docs/Sales #1.html',text:async()=>saved},
  {name:'old.html',webkitRelativePath:'docs/old.html',text:async()=>'<script>const DATA = {"title":"Old report"};</script>'},
  {name:'skip.html',webkitRelativePath:'docs/nested/skip.html',text:async()=>saved}]);
 assert.match(hub.document.getElementById('batch-status').textContent,/Synthetic failure/);
 assert.match(hub.document.getElementById('tree').textContent,/Latest batch failed/);
 assert.equal(hub.document.querySelectorAll('.card').length,2);assert.equal(hub.document.querySelector('#tree img'),null);
 assert.ok(hub.document.querySelector('.card h3 a').href.startsWith('blob:'));
 hub.downloadCatalog();assert.equal(filename,'pbi-home.html');
 hub=open(await blob.text());assert.match(hub.document.getElementById('batch-status').textContent,/Synthetic failure/);assert.equal(hub.document.querySelectorAll('.card').length,2);
 assert.ok(!hub.document.querySelector('.card h3 a').href.startsWith('blob:'));
 assert.deepEqual(errors,[]);
 windows.forEach(w=>w.close());console.log('DOM integration passed: edit/remove, saved HTML reopen, inert folder scan, escaped metadata, downloaded home reopen.');
})().catch(e=>{console.error(e);process.exit(1)});
