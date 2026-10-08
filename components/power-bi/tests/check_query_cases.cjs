// Query cases in the actual generated script, on a document with a table query and a shared query both named Sales
// and a query whose result is a step written before another it uses ("Out of order").
//   node check_query_cases.cjs <document.html>
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[2],'utf8');
const nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{id,value:'',innerHTML:'',textContent:'',hidden:false,style:{},
  classList:{add(){},remove(){}},setAttribute(k,v){this['@'+k]=v;},focus(){}});return nodes.get(id);};
const browserWindow={scrollTo(){},location:{hash:''},addEventListener(){},history:{
  pushState(_s,_t,hash){browserWindow.location.hash=hash;},replaceState(_s,_t,hash){browserWindow.location.hash=hash;}}};
const context=vm.createContext({console,setTimeout:()=>0,clearTimeout(){},document:{getElementById:node,querySelectorAll:()=>[],
  createElement:()=>({}),body:{appendChild(){}}},window:browserWindow});
vm.runInContext(html.match(/<script>([\s\S]*)<\/script>/)[1],context);
const run=s=>vm.runInContext(s,context);
const open=args=>run(`VIEWER_VIEWS['pbi.query'](${JSON.stringify(args)})`);
const selected=()=>run('[PQ_BY_ID.get(pqSelected).objectId,PQ_BY_ID.get(pqSelected).origin]');
const same=(a,b)=>assert.equal(JSON.stringify(a),JSON.stringify(b));

same(run("PQ.queries.filter(q=>q.name==='Sales').map(q=>q.objectId).sort()"),['pbi:query:name:Sales','pbi:query:name:Sales~2']);
const shared=run("PQ.queries.find(q=>q.name==='Sales'&&q.origin==='shared').objectId");
const table=run("PQ.queries.find(q=>q.name==='Sales'&&q.origin==='table').objectId");
run("switchTab('overview')");
assert.equal(open({query:'Sales',object:shared}),true);same(selected(),[shared,'shared']);
run("switchTab('overview')");
assert.equal(open({query:'Sales',object:table}),true);same(selected(),[table,'table']);
// an id this document does not hold: the name still opens a query, and the viewer does not call that exact
assert.equal(open({query:'Sales',object:'pbi:query:name:Gone'}),false);
assert.equal(run('activeTab'),'power-query');
// a target from before ids were sent: found by name, as before
assert.equal(open({query:'Stage'}),true);assert.equal(run('PQ_BY_ID.get(pqSelected).name'),'Stage');
// a let returns a step that is not the last one: the view names it and never says the later steps are unused
const plain=run("pqSteps(PQ.queries.find(q=>q.name==='Out of order'))").replace(/<[^>]+>/g,'').replace(/\s+/g,' ');
assert.match(plain,/The query returns the step Result, which is not the last one written\. M works out every step the result refers to, in whatever order the steps are written, so a step written after it can still be part of the result\./);
assert.doesNotMatch(plain,/not part of the result/);
console.log('Query cases: each of two queries named Sales opens as itself; a returned step is named without claiming the rest unused.');
