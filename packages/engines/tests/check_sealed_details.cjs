// The details editor in a document's own script: read-only where the document carries the library manifest.
//   node check_sealed_details.cjs <document.html> <power_bi|adf> <sealed|editable>
// Saving from the page rewrites the whole file, which breaks the manifest's integrity hash, so a sealed copy
// shows its details without an editor and its save function does nothing.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const [file,engine,expect]=process.argv.slice(2);
const html=fs.readFileSync(file,'utf8');
const hasManifest=/<script\b[^>]*\bid="pbidoc-manifest"/.test(html);
assert.equal(hasManifest,expect==='sealed','the fixture is the kind of copy the check expects');
const nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{id,value:'',innerHTML:'',textContent:'',hidden:false,style:{},dataset:{},
  classList:{add(){},remove(){},toggle(){},contains:()=>false},setAttribute(){},removeAttribute(){},getAttribute:()=>null,
  focus(){},scrollIntoView(){},contains:()=>false,querySelector:()=>null,querySelectorAll:()=>[],addEventListener(){},appendChild(){}});
  return nodes.get(id);};
const saved=[];
const doc={getElementById:id=>id==='pbidoc-manifest'?(hasManifest?node(id):null):node(id),querySelectorAll:()=>[],querySelector:()=>null,
  createElement:()=>({style:{},click(){},setAttribute(){},appendChild(){}}),body:{appendChild(){},classList:{add(){},remove(){},toggle(){}}},
  documentElement:{cloneNode:()=>({querySelector:()=>({textContent:'',innerHTML:''}),outerHTML:'<html></html>'}),classList:{add(){},remove(){},toggle(){}}},
  addEventListener(){},title:''};
const win={scrollTo(){},location:{hash:'',search:''},addEventListener(){},history:{pushState(){},replaceState(){}},matchMedia:()=>({matches:false,addEventListener(){}}),
  innerHeight:800,innerWidth:1200,parent:null};
win.parent=win;
const context=vm.createContext({console,document:doc,window:win,setTimeout:()=>0,clearTimeout(){},requestAnimationFrame:()=>0,
  localStorage:{getItem:()=>null,setItem(){}},sessionStorage:{getItem:()=>null,setItem(){}},navigator:{clipboard:{}},
  Blob:function(parts){saved.push(parts);},URL:{createObjectURL:()=>'blob:',revokeObjectURL(){}},location:win.location,history:win.history});
const scripts=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
vm.runInContext(scripts[scripts.length-1],context);
const run=s=>vm.runInContext(s,context);
const view=engine==='power_bi'?run('rDocumentation()'):run('rDetails()');
const note="<strong>Read-only.</strong> This copy is protected against changes, so its details cannot be edited here. They are recorded when the documentation is generated.";
if(expect==='sealed'){
  assert.ok(view.includes(note),'a sealed copy says why its details cannot be edited');
  assert.ok(!view.includes('Download updated HTML'),'and offers no save');
  assert.ok(!/survive regeneration|Download the updated HTML|replace the original/.test(view),'nor instructions for saving that do not apply');
  assert.ok(!/<input|<textarea/.test(view.split('<h2>How this document was generated')[0]),'and no field to type in');
  run(engine==='power_bi'?'saveDocumentationHtml()':'saveDetailsHtml()');
  assert.equal(saved.length,0,'its save function writes nothing');
}else{
  assert.ok(view.includes('Download updated HTML'),'a copy without the manifest keeps its editor');
  assert.ok(!view.includes(note));
}
console.log(`${engine} ${expect}: details editor checks passed.`);
