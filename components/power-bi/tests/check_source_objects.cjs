const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
const nodes=new Map();const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',innerHTML:'',classList:{add(){},remove(){}},setAttribute(){}});return nodes.get(id)};
let blob,filename;
const c=vm.createContext({console,Blob,setTimeout:fn=>fn(),URL:{createObjectURL:b=>(blob=b,'blob:test'),revokeObjectURL(){}},document:{getElementById:node,querySelectorAll:()=>[],body:{appendChild(){}},createElement:()=>({click(){filename=this.download},remove(){}})},window:{scrollTo(){}}});
const run=s=>vm.runInContext(s,c);
run(fs.readFileSync(process.argv[2],'utf8').match(/<script>([\s\S]*)<\/script>/)[1]);
run("switchTab('sources');setPageScope('p2')");assert.equal(run('visibleSourceObjects.length'),2);
assert.ok(run("visibleSourceObjects.every(r=>r.pageId==='p2')"));
node('source-object-search').value='Customers';run('filterSourceObjects()');assert.equal(run('visibleSourceObjects.length'),2);
node('source-object-status').value='Unresolved';run('filterSourceObjects()');assert.equal(run('visibleSourceObjects.length'),0);
node('source-object-status').value='';node('source-object-search').value='';run("setPageScope('*')");
assert.ok(!node('source-object-rows').innerHTML.includes('<script>marker</script>'));
assert.ok(node('source-object-rows').innerHTML.includes('&lt;script&gt;marker'));
node('source-object-search').value='Orders';run('filterSourceObjects()');
// The SQL text contains Orders on both object rows; searches include code.
assert.equal(run('visibleSourceObjects.length'),6);
run('downloadSourceObjectsCsv()');assert.equal(filename,'Sales-source-objects.csv');
fs.writeFileSync(process.argv[3],Buffer.from(await blob.arrayBuffer()));
run("setPageScope('p2');downloadSourceObjectsCsv(false)");
assert.equal(filename,'Sales-source-objects-no-code.csv');
fs.writeFileSync(process.argv[3]+'.no-code.csv',Buffer.from(await blob.arrayBuffer()));
console.log('Source objects CSV and UI checks passed.');
})().catch(e=>{console.error(e);process.exit(1)});
