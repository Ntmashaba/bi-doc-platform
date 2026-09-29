// Stateful DOM adapter for generated JavaScript. Does not verify browser layout.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[2],'utf8');
const nodes=new Map();let mounted=[];
const decode=s=>s.replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
function node(id){
  if(!nodes.has(id)){
    let content='';
    nodes.set(id,{id,value:'',hidden:false,style:{},classList:{add(){},remove(){}},setAttribute(){},focus(){},querySelectorAll(){return []},
      get innerHTML(){return content;},set innerHTML(s){content=s;if(id==='main') mount(s);}});
  }
  return nodes.get(id);
}
function mount(html){
  mounted=[];
  for(const m of html.matchAll(/<(input|select|details)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){
    const el=node(m[3]);el.value=decode(m[2].match(/\bvalue="([^"]*)"/)?.[1]||'');el.tagName=m[1].toUpperCase();
    if(el.tagName==='SELECT'){
      const body=html.slice(m.index).split('</select>')[0];
      const options=[...body.matchAll(/<option(?: value="([^"]*)")?([^>]*)>([^<]*)<\/option>/g)];
      const o=options.find(x=>x[2].includes('selected'))||options[0];el.value=decode(o?.[1]??o?.[3]??'');
    }
    mounted.push(el);
  }
}
const historyEntries=[], listeners={};
const browserWindow={scrollTo(){},location:{hash:''},addEventListener(name,fn){listeners[name]=fn;},history:{
 pushState(_state,_title,hash){historyEntries.push(hash);browserWindow.location.hash=hash;},
 replaceState(_state,_title,hash){browserWindow.location.hash=hash;}
}};
const context=vm.createContext({console,setTimeout,document:{getElementById:node,
 querySelectorAll:s=>s.startsWith('#main input')?mounted.filter(e=>['INPUT','SELECT'].includes(e.tagName)):[],
 createElement:()=>({}),body:{appendChild(){}}},window:browserWindow});
vm.runInContext(html.match(/<script>([\s\S]*)<\/script>/)[1],context);
const run=s=>vm.runInContext(s,context);
const same=(a,b)=>assert.equal(JSON.stringify(a),JSON.stringify(b));

// Structural checks on the actual generated diagrams (not a browser paint test).
let checked=0;
function checkSvg(id,expected){
 const svg=node(id).innerHTML;
 if(!expected){assert.ok(!svg.includes('<rect'));return;}
 assert.ok(!/NaN|Infinity/.test(svg),id+' has invalid coordinates');
 const bounds=svg.match(/viewBox="0 0 ([\d.]+) ([\d.]+)"/);assert.ok(bounds,id+' has a viewBox');
 const width=+bounds[1],height=+bounds[2];
 const rects=[...svg.matchAll(/<rect\b([^>]+)>/g)];assert.equal(rects.length,expected,id+' retains every shape');
 for(const [,attrs] of rects){
  const number=k=>+attrs.match(new RegExp('\\b'+k+'="([\\d.-]+)"'))[1];
  const x=number('x'),y=number('y'),w=number('width'),h=number('height');
  assert.ok(x>=2&&y>=2&&x+w<=width-2&&y+h<=height-2,id+' shape clipped by SVG bounds');
  assert.ok(w>=200&&h>=60,id+' uses readable shape sizes');
  assert.ok(number('stroke-width')>=1.5,id+' has a visible outline');
 }
 const groups=[...svg.matchAll(/<g\b([^>]*class="erd-node"[^>]*)>/g)];
 assert.equal(groups.length,expected);
 for(const [,attrs] of groups){assert.match(attrs,/opacity="1"/,'shapes must remain opaque while selecting');assert.match(attrs,/tabindex="0"/);}
 // Every edge must precede every node, so lines cannot paint over node labels.
 assert.ok(svg.lastIndexOf('<path')<svg.indexOf('<g class="erd-node"'));
 checked++;
}
if(run('has.linked')){
 const scopes=['*',...run('R.pages.map(p=>p.id)')];
 for(const scope of scopes){
  run(`pageScope=${JSON.stringify(scope)};switchTab('lineage')`);
  const expected=run('(()=>{const g=lineageGraph();return g.sources.length+g.tables.length+g.pages.length})()');
  checkSvg('lineage-board',expected);
  const selections=run("(()=>{const g=lineageGraph();return [...g.sources.map(s=>['s',s.key]),...g.tables.map(t=>['t',t.name]),...g.pages.map(p=>['p',p.id])]})()");
  for(const [kind,key] of selections){run(`selectLineage(${JSON.stringify(kind)},${JSON.stringify(key)})`);checkSvg('lineage-board',expected);}
  run('zoomLineage(-10)');checkSvg('lineage-board',expected);
  run('zoomLineage(10)');checkSvg('lineage-board',expected);run('resetLineage()');
 }
}
if(run('has.model')){
 run("pageScope='*';switchTab('rels')");
 const expected=run('M.tables.filter(t=>M.relationships.some(r=>r.fromTable===t.name||r.toTable===t.name)).length');
 checkSvg('erd',expected);
 for(const name of run('M.tables.map(t=>t.name)')){run(`focusERD(${JSON.stringify(name)})`);checkSvg('erd',expected);}
 run('zoomERD(-10)');checkSvg('erd',expected);run('zoomERD(10)');checkSvg('erd',expected);run('resetERD()');
}
if(run('has.report')){
 run("pageScope='*';switchTab('layout')");
 for(const page of run('R.pages')){
  run(`zoomPageLayout(${JSON.stringify(page.id)},10)`);
  assert.equal(node('layout-zoom-'+run(`pageKey(${JSON.stringify(page.id)})`)).textContent,'300%');
  run(`zoomPageLayout(${JSON.stringify(page.id)},0)`);
  assert.equal(node('layout-canvas-'+run(`pageKey(${JSON.stringify(page.id)})`)).style.width,'100%');
  const geometry=run(`layoutGeometry(R.pages.find(p=>p.id===${JSON.stringify(page.id)}))`);
  assert.equal(geometry.placed.length+geometry.unplaced.length,page.visuals.length);
  for(const v of geometry.placed){assert.ok(v.x>=geometry.left&&v.y>=geometry.top);assert.ok(v.x+v.width<=geometry.left+geometry.width+.001&&v.y+v.height<=geometry.top+geometry.height+.001);}
 }
 assert.equal((node('main').innerHTML.match(/class="visual-box /g)||[]).length,run('R.pages.reduce((n,p)=>n+layoutGeometry(p).placed.length,0)'));
}
console.log(run('DATA.title')+': '+checked+' diagram states checked; page-layout inventory and bounds verified.');
