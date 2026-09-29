const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
const nodes=new Map();const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',innerHTML:'',classList:{add(){},remove(){}},setAttribute(){}});return nodes.get(id)};
const downloads=[];let blob;
const context=vm.createContext({console,Blob,setTimeout:fn=>fn(),URL:{createObjectURL:b=>(blob=b,'blob:test'),revokeObjectURL(){}},
 document:{getElementById:node,querySelectorAll:()=>[],body:{appendChild(){}},createElement:()=>({click(){downloads.push(this.download)},remove(){}})},window:{scrollTo(){}}});
const run=s=>vm.runInContext(s,context);
run(fs.readFileSync(process.argv[2],'utf8').match(/<script>([\s\S]*)<\/script>/)[1]);
run("switchTab('sources');setPageScope('p2')");assert.match(node('main').innerHTML,/Export all M queries CSV/);
run('downloadQueryCsv()');assert.equal(downloads.at(-1),'Sales _ Q4_ Café_-source-queries.csv');
const bytes=Buffer.from(await blob.arrayBuffer());assert.equal(bytes.subarray(0,3).toString('hex'),'efbbbf');fs.writeFileSync(process.argv[3],bytes);
run('downloadColumnCsv()');assert.equal(downloads.at(-1),'Sales _ Q4_ Café_-column-page-usage.csv');
run("switchTab('tables');downloadSourceCsv()");assert.equal(downloads.at(-1),'Sales _ Q4_ Café_-report-table-sources.csv');
run("exportCsvFile(columnCsv(cleanupRows()),'cleanup-column-page-usage.csv')");assert.equal(downloads.at(-1),'Sales _ Q4_ Café_-cleanup-column-page-usage.csv');
run('exportComparison()');assert.equal(downloads.at(-1),'Sales _ Q4_ Café_-extract-changes-by-page.csv');
run("R.name='...'");assert.equal(run("csvFilename('source-queries.csv')"),'Power-BI-source-queries.csv');
run("R.name='=Budget\\n2026'");assert.ok(!run("csvFilename('source-queries.csv')").includes('\n'));
console.log('CSV downloads verified: exact headers, all queries, safe report names, BOM and formula protection.');
})().catch(e=>{console.error(e);process.exit(1)});
