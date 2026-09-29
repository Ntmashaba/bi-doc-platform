// Diagnostic probes. Harmless marker assignment verifies unsafe event encoding.
const fs=require('node:fs'),vm=require('node:vm');
const nodes=new Map();const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',innerHTML:'',hidden:false,classList:{add(){},remove(){}},setAttribute(){},focus(){},querySelectorAll(){return []}});return nodes.get(id)};
const c=vm.createContext({console,setTimeout,document:{getElementById:node,querySelectorAll:()=>[]},window:{scrollTo(){}}});
const run=s=>vm.runInContext(s,c);
run(fs.readFileSync(process.argv[2],'utf8').match(/<script>([\s\S]*)<\/script>/)[1]);
const obs=(label,value)=>console.log(label+': '+JSON.stringify(value));
obs('F05 calculation-group comparison',run(`(()=>{const a=JSON.parse(JSON.stringify(DATA)),b=JSON.parse(JSON.stringify(DATA));a.model.tables[0].calculationGroup=[{name:'X',expression:'SELECTEDMEASURE()'}];b.model.tables[0].calculationGroup=[{name:'X',expression:'SELECTEDMEASURE()*2'}];return compareExtracts(a,b)})()`));
obs('F05 shared M comparison',run(`(()=>{const a=JSON.parse(JSON.stringify(DATA)),b=JSON.parse(JSON.stringify(DATA));a.model.expressions=[{name:'Stage',kind:'m',expression:'1'}];b.model.expressions=[{name:'Stage',kind:'m',expression:'2'}];return compareExtracts(a,b)})()`));
obs('F07 colliding table IDs',run("[slug('Sales-US'),slug('Sales US')]"));
// Draw the actual SVG, decode the handler as an HTML parser does, then execute
// only in this isolated VM. No network/filesystem operations are exposed to it.
run(`M.tables[0].name="x');globalThis.reviewMarker=1;//";drawLineage()`);
const svg=node('lineage-board').innerHTML;
const handler=[...svg.matchAll(/onclick="([^"]*)"/g)].map(m=>m[1]).find(s=>s.includes('reviewMarker'));
const decoded=handler.replace(/&#39;/g,"'").replace(/&quot;/g,'"').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
run(decoded);obs('F06 inline-handler marker executed',run('globalThis.reviewMarker===1'));
// Restore and demonstrate the independent inspector selection/state mismatch.
run(`M.tables[0].name='Sales';impactNode=nodeId('c','Sales','Amount');switchTab('impact')`);
node('impact-field').value=run('impactNode');
run(`viewState.set('impact',{inputs:[['impact-field',nodeId('c','Sales','Amount')]],details:[]});inspectNode(nodeId('m','Sales','Total'));activeTab='cleanup';switchTab('impact')`);
obs('F08 displayed select',node('impact-field').value);
obs('F08 rendered details show Total',node('main').innerHTML.includes('<p>measure · Sales[Total]</p>'));
