/* Offline exploration. Source data remains immutable when a page is selected. */
let pageScope = '*', activeTab = '', viewState = new Map();
const expandedTables = new Set();
let matrixRelOnly = false, matrixQuery = '', impactQuery = '', impactNode = '', cleanupDecision = 'Deletion candidate';
let comparison = null, comparisonName = '', comparisonError = '';
const graph = DATA.columns?.dependencyGraph || {nodes:[],edges:[],consumers:[],wholeTableDependencies:[]};
const graphNodes = new Map(graph.nodes.map(n=>[n.id,n]));
const reverseGraph = new Map();
for(const e of graph.edges){
  if(!reverseGraph.has(e.dependency)) reverseGraph.set(e.dependency,[]);
  reverseGraph.get(e.dependency).push(e.dependent);
}
const inPageScope = r => pageScope==='*'||(r.pageId||'')===pageScope;
const scopedPages = () => (R?.pages||[]).filter(p=>pageScope==='*'||p.id===pageScope);
const tableInScope = name => pageScope==='*'||(DATA.columns?.tablePages||[]).some(r=>r.table===name&&inPageScope(r));
const scopedTables = () => (M?.tables||[]).filter(t=>tableInScope(t.name));
const action = (fn,...args) => esc(fn+'('+args.map(a=>JSON.stringify(a)).join(',')+')');
const nodeId = (kind,table,name) => JSON.stringify([kind,table,name]);
const listText = a => a?.length?a.map(esc).join('<br>'):'—';
function rememberView(){
  if(!activeTab || activeTab==='report-details') return;
  const state={inputs:[],details:[]};
  document.querySelectorAll('#main input[id]:not([type=file]), #main select[id]').forEach(el=>{
    if(!['global-page','impact-field','impact-search'].includes(el.id)) state.inputs.push([el.id,el.value]);
  });
  document.querySelectorAll('#main details[id][open]').forEach(el=>state.details.push(el.id));
  viewState.set(activeTab,state);
}
function restoreView(id){
  if(id==='report-details') return;
  const state=viewState.get(id);
  if(id==='impact'){document.getElementById('impact-field').value=impactNode;document.getElementById('impact-search').value=impactQuery;}
  for(const [key,value] of state?.inputs||[]){if(id==='impact'&&['impact-field','impact-search'].includes(key)) continue;const el=document.getElementById(key);if(el) el.value=value;}
  for(const key of state?.details||[]){const el=document.getElementById(key);if(el) el.open=true;}
  {const el=document.getElementById('global-page');if(el) el.value=pageScope;}
}
function scopeBar(id){
  const globalViews=['overview','rels','security','warnings','cleanup','report-details'];
  const pages=[...(R?.pages||[])];
  if(id==='compare') for(const p of comparison?.report?.pages||[]) if(!pages.some(x=>x.id===p.id)) pages.push(p);
  if(id==='report-details') return '';
  const whole=globalViews.includes(id);
  const dupes=new Set(pages.map(p=>p.name).filter((n,i,a)=>a.indexOf(n)!==i));
  const note=whole?'This view covers the whole extract':
    id==='compare'?'Page changes follow the selected page':
    'Usage and exports follow the selected page; deletion assessments cover the whole extract';
  const issues=DATA.columns?.issues||[];
  const coverage=!DATA.columns?'':issues.length
    ?`<details class="coverage"><summary><span class="badge b-warn">${issues.length} analysis ${issues.length===1?'issue':'issues'}</span></summary><div class="coverage-pop"><b>Analysis coverage</b><p>${listText(issues)}</p></div></details>`
    :'<span class="badge b-direct">Full analysis coverage</span>';
  const select=R?`<label class="scope-page">Report page <select id="global-page" onchange="setPageScope(this.value)"${whole?' title="Retained for page-level views"':''}>
    <option value="*">All pages</option><option value="">No specific page</option>
    ${pages.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}${dupes.has(p.name)?` [${esc(p.id)}]`:''}${p.hidden?' (hidden)':''}</option>`).join('')}</select></label>`:'';
  return `<div class="scope-bar">${select}<span class="mut scope-note">${esc(note)}</span>${coverage}</div>`;
}
function setPageScope(id){
  pageScope=id;
  closeInspector();
  switchTab(activeTab||'overview');
}
function openInspector(title,html){
  let panel=document.getElementById('inspector');
  if(!panel){panel=document.createElement('aside');panel.id='inspector';document.body.appendChild(panel);}
  panel.hidden=false;
  panel.innerHTML=`<button class="chip" onclick="closeInspector()" aria-label="Close details">Close</button><h2 id="inspector-title">${esc(title)}</h2>${html}`;
  panel.setAttribute('role','region');panel.setAttribute('aria-labelledby','inspector-title');
  panel.setAttribute('tabindex','-1');panel.focus();
}
function closeInspector(){const panel=document.getElementById('inspector');if(panel) panel.hidden=true;}
function usageCell(rows){
  if(!rows.length) return {label:'—',cls:'empty-cell'};
  const kinds=new Set(rows.flatMap(r=>r.kinds||[r.pageUsage]));
  const direct=[...kinds].some(k=>k.includes('Direct'));
  const indirect=[...kinds].some(k=>/Via measures|Via calculations|Table expression/.test(k));
  return {label:direct?(indirect?'Direct + indirect':'Direct'):indirect?'Indirect':'Possible',cls:direct?'cell-direct':indirect?'cell-indirect':'cell-possible'};
}
function toggleMatrixTable(name){expandedTables.has(name)?expandedTables.delete(name):expandedTables.add(name);switchTab('matrix');}
function matrixRows(){
  const q=matrixQuery.trim().toLowerCase();
  const rows=[];
  for(const t of scopedTables()){
    const cols=t.columns.filter(c=>!q||`${t.name} ${c.name}`.toLowerCase().includes(q));
    if(q&&!cols.length&&!t.name.toLowerCase().includes(q)) continue;
    rows.push({table:t.name,column:null});
    if(expandedTables.has(t.name)||q) for(const c of cols) rows.push({table:t.name,column:c.name});
  }
  return rows;
}
function rMatrix(){
  const pages=scopedPages();
  const slots=[...pages.map(p=>({pageId:p.id,page:p.name,report:R.name}))];
  if(pageScope==='*'||pageScope==='') slots.push({pageId:'',page:'No specific page',report:R.name});
  return `<h1>Usage matrix</h1><p class="sub">Expand a table to see its columns across individual pages. Select a cell for its evidence. A dash means no page reference was detected, not that deletion is safe.</p>
    <input id="matrix-search" class="search" value="${esc(matrixQuery)}" placeholder="Search tables or columns…" aria-label="Search usage matrix" oninput="matrixQuery=this.value;renderMatrixBody()">
    <label class="mut"><input type="checkbox" id="matrix-rel"${matrixRelOnly?' checked':''} onchange="matrixRelOnly=this.checked;switchTab('matrix')"> Include relationship-only</label>
    <p class="mut">Direct = visual or filter binding · Indirect = measure, calculation or table expression · Possible = calculated-table/parameter dependency${matrixRelOnly?', or an active relationship to a table used on the page':''}. Tables reached only through relationships are ${matrixRelOnly?'shown as Possible':'hidden; tick the box to show them'}. Hidden pages are included.</p>
    <div class="column-scroll"><table class="t matrix"><thead><tr><th>Table / column</th>${slots.map(p=>`<th title="${esc(p.pageId)}">${esc(p.page)}</th>`).join('')}</tr></thead><tbody id="matrix-body">${matrixBody(slots)}</tbody></table></div>`;
}
function matrixBody(slots){
  return matrixRows().map(r=>`<tr><th>${r.column===null?`<button class="xl" aria-expanded="${expandedTables.has(r.table)}" onclick="${action('toggleMatrixTable',r.table)}">${expandedTables.has(r.table)?'−':'+'} ${esc(r.table)}</button>`:`<span class="matrix-column">${esc(r.column)}</span>`}</th>
    ${slots.map(p=>{
      const matches=(r.column===null?DATA.columns.tablePages:DATA.columns.rows).filter(x=>x.table===r.table&&(r.column===null||x.column===r.column)&&x.pageId===p.pageId);
      const counted=r.column===null?matches.map(matrixKinds).filter(x=>x.kinds.length):matches.filter(x=>x.evidence.length);
      const cell=usageCell(counted);
      const label=!p.pageId&&matches.length?(matches.some(x=>x.evidence.length)?'Unassigned usage':'No page usage'):cell.label;
      return `<td class="${cell.cls}"><button class="xl" aria-label="${esc((r.column||r.table)+' on '+p.page+' ['+p.pageId+']: '+label)}" onclick="${action('inspectCell',r.table,r.column,p.pageId)}">${esc(label)}</button></td>`;
    }).join('')}</tr>`).join('')||`<tr><td colspan="${slots.length+1}">No tables match this selection.</td></tr>`;
}
// Relationship reachability alone says little about use, so it is opt-in.
function matrixKinds(row){
  return matrixRelOnly?row:{...row,kinds:row.kinds.filter(k=>k!=='Possible relationship dependency')};
}
function renderMatrixBody(){
  const slots=scopedPages().map(p=>({pageId:p.id,page:p.name}));
  if(pageScope==='*'||pageScope==='') slots.push({pageId:'',page:'No specific page'});
  document.getElementById('matrix-body').innerHTML=matrixBody(slots);
}
function inspectCell(table,column,pageId){
  const rows=(column===null?DATA.columns.tablePages:DATA.columns.rows).filter(r=>r.table===table&&(column===null||r.column===column)&&r.pageId===pageId);
  const page=(R.pages||[]).find(p=>p.id===pageId);
  const evidence=[...new Set(rows.flatMap(r=>r.evidence))];
  const columns=DATA.columns.rows.filter(r=>r.table===table&&r.pageId===pageId&&(column===null||r.column===column));
  openInspector(column===null?table:`${table}[${column}]`, `<p>${esc(R.name)} / ${esc(page?.name||'No specific page')} ${esc(pageId)}</p>
    <h3>Evidence on this page</h3><p>${listText(evidence)||'—'}</p>${!evidence.length?'<p>No usage evidence detected for this cell.</p>':''}
    <h3>Column assessments (whole extract)</h3>${columns.map(r=>`<p><button class="xl" onclick="${action('inspectNode',nodeId('c',r.table,r.column),pageId)}">${esc(r.column)}</button> · ${esc(r.decision)}</p>`).join('')||'<p>No resolved column references.</p>'}
    ${column!==null?`<button class="chip" onclick="${action('inspectNode',nodeId('c',table,column),pageId)}">Trace dependency paths</button>`:''}`);
}
// A breadth-first walk gives one shortest, explicit reference path per dependent.
// All reachable dependents are returned, including cycles, without inventing edges.
function downstreamPaths(id){
  const paths=new Map([[id,[id]]]), queue=[id];
  for(let i=0;i<queue.length;i++) for(const next of reverseGraph.get(queue[i])||[]){
    if(paths.has(next)) continue;
    paths.set(next,[...paths.get(queue[i]),next]);queue.push(next);
  }
  return paths;
}
function impactConsumers(id,scope=pageScope){
  const paths=downstreamPaths(id);
  return graph.consumers.filter(c=>paths.has(c.node)&&(scope==='*'||c.pageId===scope)).map(c=>({...c,path:paths.get(c.node),possible:paths.get(c.node).some((id,i,path)=>i>0&&graph.edges.some(e=>e.dependency===path[i-1]&&e.dependent===id&&e.possible))}));
}
function impactDetails(id,scope=pageScope){
  const n=graphNodes.get(id);if(!n) return '<p>No resolved field selected.</p>';
  const paths=downstreamPaths(id), consumers=impactConsumers(id,scope);
  const col=DATA.columns.rows.find(r=>r.table===n.table&&r.column===n.name&&n.kind==='column');
  const expression=n.kind==='measure'?M.measures.find(m=>m.table===n.table&&m.name===n.name)?.expression:col?.expression;
  const dependencies=graph.edges.filter(e=>e.dependent===id).map(e=>graphNodes.get(e.dependency));
  const wholeTables=graph.wholeTableDependencies.filter(e=>e.node===id).map(e=>e.table);
  const dependent=[...paths.keys()].filter(k=>k!==id).map(k=>graphNodes.get(k));
  const sources=[...new Map(DATA.tableSources.filter(r=>r.table===n.table).map(r=>[r.partition,r])).values()];
  const pathHtml=consumers.map(c=>`<li><b>${esc(pageLabel(c))}</b><div>${esc(c.evidence)}</div>${c.possible?'<b>Possible dependency — runtime selection/output lineage unresolved</b>':''}<p class="path">${c.path.map(k=>esc(graphNodes.get(k)?.label||k)).join(' → ')}</p>${c.visualId?`<button class="xl" onclick="${action('inspectVisual',c.pageId,c.visualId)}">Inspect visual ${esc(c.visualId)}</button>`:''}</li>`).join('');
  return `<p>${esc(n.kind)} · ${esc(n.label)}</p>${col?`<p><b>${esc(col.decision)}</b> · ${esc(col.reason)}</p>`:''}
    <h3>Where it is used</h3><p class="mut">${consumers.length} binding locations in the selected scope. One shortest detected path per binding is shown; DAX is not executed.</p><ul class="impact-paths">${pathHtml||'<li>No resolved usage in this page scope.</li>'}</ul>
    <h3>Reads these fields</h3><p>${dependencies.map(d=>`<button class="xl" onclick="${action('inspectNode',d.id,scope)}">${esc(d.label)}</button>`).join(', ')||'No explicit field dependencies detected'}</p>
    ${wholeTables.length?`<p>Whole-table references: ${listText(wholeTables)}. Individual column use cannot be confirmed from these references alone.</p>`:''}
    <h3>Dependent calculations (whole model)</h3><p>${dependent.map(d=>`<button class="xl" onclick="${action('inspectNode',d.id,scope)}">${esc(d.label)}</button>`).join(', ')||'None detected'}</p>
    ${col?`<h3>Other dependencies and uncertainty</h3><p>${listText(col.modelDependencies)}</p><p>${listText(col.reviewNotes)}</p>`:''}
    ${expression?`<h3>Expression</h3><pre class="code">${esc(expression)}</pre>`:''}
    <h3>Home table sources</h3><p class="mut">For measures, the home table is organisational. Follow “Reads these fields” above to trace data sources.</p>
    ${sources.map(r=>`<p>${esc([r.server,r.database,r.object||r.partition].filter(Boolean).join(' / '))}</p>${r.query?`<details><summary>${esc(r.queryKind)} query</summary><pre class="code">${esc(r.query)}</pre></details>`:''}`).join('')}
    ${col?`<p>Model input column: ${esc(col.sourceColumn)||'Unknown'}</p>`:''}<p class="mut">${esc(DATA.columns.sourceNote)}</p>`;
}
function inspectNode(id,scope=pageScope){impactNode=id;if(!graphNodes.get(id)?.label.toLowerCase().includes(impactQuery.toLowerCase())) impactQuery='';openInspector(graphNodes.get(id)?.label||'Field details',impactDetails(id,scope));}
function rImpact(){
  const nodes=graph.nodes.filter(n=>!impactQuery||n.label.toLowerCase().includes(impactQuery.toLowerCase()));
  if(!nodes.some(n=>n.id===impactNode)) impactNode=nodes[0]?.id||'';
  return `<h1>Impact inspector</h1><p class="sub">Choose a column or measure to see downstream calculations and the specific report pages and visuals that consume it.</p>
    <input id="impact-search" class="search" value="${esc(impactQuery)}" placeholder="Search a column or measure…" aria-label="Search dependency fields" oninput="impactQuery=this.value;renderImpactOptions()">
    <label>Field <select id="impact-field" class="search" onchange="impactNode=this.value;renderImpactDetails()">${impactOptions(nodes)}</select></label>
    <div id="impact-details">${impactDetails(impactNode||nodes[0]?.id)}</div>`;
}
function impactOptions(nodes){return nodes.map(n=>`<option value="${esc(n.id)}"${n.id===impactNode?' selected':''}>${esc(n.label)} · ${n.kind}</option>`).join('');}
function renderImpactOptions(){
  const nodes=graph.nodes.filter(n=>n.label.toLowerCase().includes(impactQuery.toLowerCase()));
  if(!nodes.some(n=>n.id===impactNode)) impactNode=nodes[0]?.id||'';
  document.getElementById('impact-field').innerHTML=impactOptions(nodes);renderImpactDetails();
}
function renderImpactDetails(){document.getElementById('impact-details').innerHTML=impactDetails(impactNode);}
function layoutGeometry(page){
  const valid=v=>['x','y','width','height'].every(k=>typeof v[k]==='number'&&Number.isFinite(v[k]))&&v.width>0&&v.height>0;
  const placed=page.visuals.filter(valid), unplaced=page.visuals.filter(v=>!valid(v));
  const left=Math.min(0,...placed.map(v=>v.x)), top=Math.min(0,...placed.map(v=>v.y));
  const width=Math.max(1,Number(page.width)||0,...placed.map(v=>v.x+v.width))-left;
  const height=Math.max(1,Number(page.height)||0,...placed.map(v=>v.y+v.height))-top;
  return {placed,unplaced,left,top,width,height};
}
/* Page layout (B4): boxes coloured by visual kind, listing their fields. */
const VISUAL_KINDS=[['Card',/card|kpi|gauge/i],['Slicer',/slicer/i],['Table',/table|matrix|pivot/i],
  ['Chart',/chart|map|funnel|scatter|treemap|waterfall|ribbon|decomposition|keyInfluencers/i],['Text or image',/text|image|shape|button|title/i]];
const visualKind=v=>(VISUAL_KINDS.find(([,re])=>re.test(v.type||''))||['Other'])[0];
const KIND_CLASS={'Card':'vk-card','Slicer':'vk-slicer','Table':'vk-table','Chart':'vk-chart','Text or image':'vk-text','Other':'vk-other'};
function visualFields(pageId,v){
  const ids=[...new Set(graph.consumers.filter(c=>c.pageId===pageId&&c.visualId===v.id).map(c=>c.node))];
  const nodes=ids.map(id=>graphNodes.get(id)).filter(Boolean);
  // Same messages the analyser records, so the layout and Cleanup agree.
  const issues=new Set(DATA.columns?.issues||[]);
  const unresolved=v.fields.filter(f=>f.field&&issues.has(`Unresolved report binding ${f.table||'?'}[${f.field}]`));
  return {measures:nodes.filter(n=>n.kind==='measure'),columns:nodes.filter(n=>n.kind==='column'),unresolved};
}
const layoutZooms=new Map();
function zoomPageLayout(pageId,delta){
 const zoom=delta===0?1:Math.max(.5,Math.min(3,(layoutZooms.get(pageId)||1)+delta));
 layoutZooms.set(pageId,zoom);
 const canvas=document.getElementById('layout-canvas-'+pageKey(pageId));
 if(canvas)canvas.style.width=zoom*100+'%';
 const label=document.getElementById('layout-zoom-'+pageKey(pageId));
 if(label)label.textContent=Math.round(zoom*100)+'%';
}
// Untitled visuals: a readable type plus the first field ("Card · Amount"), not "cardVisual".
const VISUAL_TYPE_NAMES={textbox:'Text box',cardVisual:'Card',card:'Card',multiRowCard:'Multi-row card',tableEx:'Table',pivotTable:'Matrix',slicer:'Slicer',advancedSlicerVisual:'Slicer',actionButton:'Button',image:'Image',shape:'Shape',basicShape:'Shape',kpi:'KPI',gauge:'Gauge',map:'Map',filledMap:'Filled map',decompositionTreeVisual:'Decomposition tree',keyDriversVisual:'Key influencers',qnaVisual:'Q&A',pageNavigator:'Page navigator',bookmarkNavigator:'Bookmark navigator',aiNarratives:'Smart narrative',esriVisual:'ArcGIS map',group:'Group',tableEx:'Table',funnel:'Funnel',treemap:'Treemap'};
// Custom visuals carry a GUID or timestamp: PBI_CV_885E..., simpleImageEBC4..., ClusterMap1652434605854.
const CUSTOM_VISUAL_NAMES={PowerApps:'Power Apps',FlowVisual:'Power Automate'};
function visualTypeName(t){
 t=String(t||'Visual');
 if(VISUAL_TYPE_NAMES[t]) return VISUAL_TYPE_NAMES[t];
 // Display name from the report's own custom visual package (Mapbox Visual).
 const packaged=(typeof DATA!=='undefined'&&DATA.report&&DATA.report.customVisuals||{})[t];
 if(packaged) return packaged+' (custom)';
 const base=t.replace(/_?PBI_CV_[0-9A-Fa-f_]+$/,'').replace(/_?[0-9A-Fa-f]{8}_?[0-9A-Fa-f]{4}_?[0-9A-Fa-f]{4}_?[0-9A-Fa-f]{4}_?[0-9A-Fa-f]{12}$/,'').replace(/[0-9A-F]{32}$/,'').replace(/\d{8,}$/,'');
 const custom=base!==t;
 if(custom&&!base) return 'Custom visual';
 const name=CUSTOM_VISUAL_NAMES[base]||base.replace(/Visual$/,'').replace(/_/g,' ').replace(/([a-z])([A-Z])/g,'$1 $2').replace(/^./,c=>c.toUpperCase());
 return custom?name+' (custom)':name;
}
function visualName(pageId,v){
 if(v.title) return v.title;
 const f=visualFields(pageId,v), first=f.measures[0]||f.columns[0];
 return visualTypeName(v.type)+(first?' · '+first.name:'');
}
function rLayout(){
  const legend=Object.entries(KIND_CLASS).map(([k,c])=>`<span><span class="dot ${c}"></span>${esc(k)}</span>`).join('');
  return `<h1>Page layout</h1><p class="sub">A schematic from saved visual positions, not rendered charts or live data. Boxes list the measures (Σ) and columns each visual uses; select one for its bindings and filters.</p>
    <div class="legend">${legend}<span><span class="dot" style="border:1px dashed var(--ink3);background:transparent"></span>Hidden</span><span><span class="badge b-warn">!</span> Unresolved binding</span></div>`+
    (scopedPages().map(p=>{
      const g=layoutGeometry(p);
      return `<div class="card"><h2 title="${esc(p.id)}">${esc(pageTitle(p))} ${p.hidden?'· hidden page':''}</h2>
      ${g.placed.length?`<div class="erd-toolbar layout-toolbar"><span class="mut">Select a shape for details. Zoom and scroll to read small visuals.</span><div class="erd-zoom" role="group" aria-label="Zoom ${esc(p.name)}"><button aria-label="Zoom out ${esc(p.name)}" onclick="${action('zoomPageLayout',p.id,-.25)}">−</button><output id="layout-zoom-${pageKey(p.id)}" aria-live="polite">${Math.round((layoutZooms.get(p.id)||1)*100)}%</output><button aria-label="Zoom in ${esc(p.name)}" onclick="${action('zoomPageLayout',p.id,.25)}">+</button><button onclick="${action('zoomPageLayout',p.id,0)}">Fit width</button></div></div><div class="layout-viewport" tabindex="0" role="region" aria-label="${esc(p.name)} visual layout"><div id="layout-canvas-${pageKey(p.id)}" class="page-canvas" style="width:${(layoutZooms.get(p.id)||1)*100}%;aspect-ratio:${g.width}/${g.height}">${g.placed.map(v=>{
        const f=visualFields(p.id,v), kind=visualKind(v);
        const names=[...f.measures.map(n=>'Σ '+n.name),...f.columns.map(n=>n.name)];
        const label=`${visualName(p.id,v)} (${kind}${v.hidden?', hidden':''})${f.unresolved.length?`, ${f.unresolved.length} unresolved binding(s)`:''}`;
        return `<button data-page-id="${esc(p.id)}" data-visual-id="${esc(v.id)}" aria-pressed="false" class="visual-box ${KIND_CLASS[kind]}${v.hidden?' hidden-visual':''}" style="left:${100*(v.x-g.left)/g.width}%;top:${100*(v.y-g.top)/g.height}%;width:${100*v.width/g.width}%;height:${100*v.height/g.height}%" title="${esc(label+(names.length?': '+names.join(', '):''))}" aria-label="${esc(label)}" onclick="${action('inspectVisual',p.id,v.id)}">
          <span class="vb-head">${f.unresolved.length?'<span class="badge b-warn">!</span> ':''}<b>${esc(visualName(p.id,v))}</b><small>${esc(visualTypeName(v.type))}${v.hidden?' · hidden':''}</small></span>
          <span class="vb-fields">${names.map(esc).join('<br>')}</span></button>`;}).join('')}</div></div>`:'<p class="mut">No visuals with usable coordinates.</p>'}
      <details><summary>All visuals (${p.visuals.length}); ${g.unplaced.length} without usable coordinates</summary><div class="body">${p.visuals.map(v=>`<p><button class="xl" onclick="${action('inspectVisual',p.id,v.id)}">${esc(visualName(p.id,v))} [${esc(v.id)}]${v.hidden?' · hidden':''}</button></p>`).join('')}</div></details></div>`;
    }).join('')||'<p>No pages in this selection.</p>');
}
function inspectVisual(pageId,visualId){
  const p=R?.pages.find(p=>p.id===pageId), v=p?.visuals.find(v=>v.id===visualId);if(!v) return;
  document.querySelectorAll('.visual-box').forEach(box=>box.setAttribute('aria-pressed',String(box.dataset.pageId===pageId&&box.dataset.visualId===visualId)));
  const roots=graph.consumers.filter(c=>c.pageId===pageId&&c.visualId===visualId);
  const unresolved=visualFields(pageId,v).unresolved;
  openInspector(visualName(p.id,v),`<p>${esc(p.name||p.label)} · ${esc(visualTypeName(v.type))} <span class="mut">(${esc(v.type)} · ${esc(v.id)})</span>${v.hidden?' · hidden':''}</p>${unresolved.length?`<p><span class="badge b-warn">Unresolved</span> ${unresolved.map(f=>esc(`${f.table||'?'}[${f.field}]`)).join(', ')} could not be matched to the model, so usage for that table is uncertain.</p>`:''}<h3>Declared bindings</h3>
    <p>${v.fields.map(f=>esc(`${f.table||'?'}[${f.field}] (${f.kind})`)).join('<br>')||'No field bindings detected.'}</p>
    <h3>Resolved fields</h3>${[...new Set(roots.map(c=>c.node))].map(id=>`<p><button class="xl" onclick="${action('inspectNode',id,pageId)}">${esc(graphNodes.get(id)?.label)}</button></p>`).join('')||'<p>No resolved model fields.</p>'}
    <h3>Visual filters</h3><p>${v.filters.map(f=>esc(`${f.table||'?'}[${f.field}] ${f.raw||''}`)).join('<br>')||'None detected'}</p>`);
}
function cleanupRows(){return DATA.columns.rows.filter(r=>!cleanupDecision||r.decision===cleanupDecision);}
function rCleanup(){
  const rows=[...new Map(cleanupRows().map(r=>[nodeId('c',r.table,r.column),r])).values()];
  return `<h1>Cleanup review</h1><p class="sub">Whole-extract assessments, independent of the page selector. ${esc(DATA.columns.scope)}</p>
    <label>Assessment <select id="cleanup-decision" class="search" onchange="cleanupDecision=this.value;switchTab('cleanup')">${['Deletion candidate','Review','Keep',''].map(d=>`<option value="${d}"${cleanupDecision===d?' selected':''}>${d||'All assessments'}</option>`).join('')}</select></label>
    ${rCleanupTables()}<h2>Columns</h2><p>${rows.length} distinct columns · <button class="xl" onclick="exportCsvFile(columnCsv(cleanupRows()),'cleanup-column-page-usage.csv')">Export evidence at column/page grain</button></p>
    <table class="t"><thead><tr><th>Column</th><th>Assessment</th><th>Why / evidence</th><th>Pages</th></tr></thead><tbody>${rows.map(r=>{
      const pages=DATA.columns.rows.filter(x=>x.table===r.table&&x.column===r.column&&x.pageId);
      return `<tr><th><button class="xl" onclick="${action('inspectNode',nodeId('c',r.table,r.column),'*')}">${esc(r.table)}[${esc(r.column)}]</button></th><td>${esc(r.decision)}</td><td>${esc(r.reason)}<details><summary>Dependencies and review notes</summary><p>${listText(r.modelDependencies)}</p><p>${listText(r.reviewNotes)}</p></details></td><td>${pages.map(p=>esc(pageLabel(p))).join('<br>')||esc(r.pageScope)}</td></tr>`;
    }).join('')||'<tr><td colspan="4">No columns have this assessment.</td></tr>'}</tbody></table>${rCleanupMeasures()}${rDuplicateMeasures()}`;
}
function rDuplicateMeasures(){
  const dup=DATA.quality?.duplicateMeasures||[];
  if(!dup.length) return '';
  const link=l=>{const m=/^(.*)\[(.*)\]$/.exec(l);return m?`<button class="xl" onclick="${action('inspectNode',nodeId('m',m[1],m[2]),'*')}">${esc(l)}</button>`:esc(l);};
  return `<h2>Duplicate measures</h2><p class="sub">The same DAX saved under different names, compared after removing whitespace, comments and letter case. Keep one and repoint visuals and dependent measures before removing the others; check which one other reports use.</p>
    <table class="t"><thead><tr><th>Measures</th><th>Match</th><th>Format strings</th><th>DAX</th></tr></thead><tbody>${dup.map(g=>`<tr><td>${g.measures.map(link).join('<br>')}</td><td>${esc(g.match)}</td><td>${g.formats.map(f=>f?`<span class="ref">${esc(f)}</span>`:'<span class="mut">none</span>').join('<br>')}</td><td><span class="path">${esc(g.expression)}</span></td></tr>`).join('')}</tbody></table>`;
}
function cleanupMeasureRows(){return (DATA.columns.measures||[]).filter(r=>!cleanupDecision||r.decision===cleanupDecision);}
function rCleanupTables(){
  const tables=(DATA.columns.tables||[]).filter(t=>!cleanupDecision||t.decision===cleanupDecision);
  if(!tables.length) return '';
  return `<div class="card"><b>Whole tables with no detected use</b><p>${tables.map(t=>`${esc(t.table)} (${t.columns} column(s), ${t.measures} measure(s)${t.sources.length?' · '+esc(t.sources.join('; ')):''})`).join('<br>')}</p><p class="mut">${esc(tables[0].reason)}</p></div>`;
}
function rCleanupMeasures(){
  if(!DATA.columns.measures) return '';
  const rows=cleanupMeasureRows();
  return `<h2>Measures</h2><p>${rows.length} measures · <button class="xl" onclick="exportCsvFile(inventoryCsv(cleanupMeasureRows(),DATA.columns.measureCsvFields),'cleanup-measures.csv')">Export measure assessments</button></p>
    <table class="t"><thead><tr><th>Measure</th><th>Assessment</th><th>Why / evidence</th><th>Pages</th></tr></thead><tbody>${rows.map(r=>`<tr><th><button class="xl" onclick="${action('inspectNode',nodeId('m',r.table,r.measure),'*')}">${esc(r.table)}[${esc(r.measure)}]</button></th><td>${esc(r.decision)}</td><td>${esc(r.reason)}${r.usedBy.length||r.modelDependencies.length||r.reviewNotes.length?`<details><summary>Dependants and review notes</summary><p>${listText(r.usedBy)}</p><p>${listText(r.modelDependencies)}</p><p>${listText(r.reviewNotes)}</p></details>`:''}</td><td>${r.pages.map(esc).join('<br>')||'No page usage detected'}</td></tr>`).join('')||'<tr><td colspan="4">No measures have this assessment.</td></tr>'}</tbody></table>`;
}
function csvFilename(name){
  const raw=R?.name||M?.name||DATA.title||'Power-BI';
  let reportName=String(raw).normalize('NFC').replace(/[<>:"/\\|?*\x00-\x1f\x7f]/g,'_').replace(/[ .]+$/g,'').trim();
  if(!reportName||/^\.+$/.test(reportName)) reportName='Power-BI';
  // Leave room for the export type and extension on common filesystems.
  reportName=Array.from(reportName).slice(0,80).join('');
  return reportName+'-'+name;
}
const queryCsvFields=[['report','report'],['queryName','query name'],['mCode','query m code']];
function downloadQueryCsv(){
  exportCsvFile(inventoryCsv(DATA.sourceQueries||[],queryCsvFields),'source-queries.csv');
}
function exportCsvFile(csv,name){
  const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));
  const a=document.createElement('a');a.href=url;a.download=csvFilename(name);document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
// Comparison uses definitions and bindings, not volatile timestamps or generated prose.
function canonical(value){
  if(Array.isArray(value)) return value.map(canonical);
  if(value&&typeof value==='object') return Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])]));
  return value;
}
const stable = value => JSON.stringify(canonical(value));
function snapshot(payload){
  const result=new Map(), report=payload.report?.name||'Not supplied';
  const columns=payload.columns?.rows||[], tablePages=payload.columns?.tablePages||[], measurePages=payload.columns?.measurePages||[];
  const put=(kind,key,label,value,pages=[])=>result.set(stable([kind,...key]),{kind,label,value:stable(value),pages:[...new Map(pages.filter(r=>r.pageId).map(r=>[r.pageId,{report:r.report||report,page:r.page,pageId:r.pageId}])).values()]});
  for(const t of payload.model?.tables||[]){
    const pages=tablePages.filter(r=>r.table===t.name);
    put('Table',[t.name],t.name,{hidden:t.isHidden,description:t.description},pages);
    put('Calculation group',[t.name],t.name,t.calculationGroupDefinition||t.calculationGroup,pages);
    put('Table detail rows',[t.name],t.name,t.detailRowsDefinition,pages);
    for(const c of t.columns||[]) put('Column',[t.name,c.name],`${t.name}[${c.name}]`,{type:c.dataType,expression:c.expression,sourceColumn:c.sourceColumn,hidden:c.isHidden,sortBy:c.sortByColumn,isKey:c.isKey},columns.filter(r=>r.table===t.name&&r.column===c.name));
    for(const p of t.partitions||[]) put('Source',[t.name,p.name],`${t.name} / ${p.name}`,{mode:p.mode,type:p.type,expression:p.expression,source:p.source},pages);
    for(const h of t.hierarchies||[]) put('Hierarchy',[t.name,h.name],`${t.name} / ${h.name}`,h,pages);
  }
  for(const m of payload.model?.measures||[]) put('Measure',[m.table,m.name],`${m.table}[${m.name}]`,{expression:m.expression,format:m.formatString,dynamicFormat:m.formatStringExpression,detailRows:m.detailRowsDefinition,hidden:m.isHidden},measurePages.filter(r=>r.table===m.table&&r.measure===m.name));
  for(const e of payload.model?.expressions||[]) put('Shared expression',[e.name],e.name,{kind:e.kind,expression:e.expression});
  for(const rel of payload.model?.relationships||[]) put('Relationship',[rel.name],rel.name,rel,tablePages.filter(r=>[rel.fromTable,rel.toTable].includes(r.table)));
  for(const role of payload.model?.roles||[]) put('Security role',[role.name],role.name,role);
  if(payload.report){
    const r=payload.report,allPages=r.pages.map(p=>({report:r.name,page:p.name,pageId:p.id}));
    put('Report filters',[],r.name,{filters:r.reportFilters,fields:r.otherFields},allPages);
    for(const b of r.bookmarks||[]) put('Bookmark',[b.name],b.name,b);
    for(const p of r.pages){
      const pages=[{report:r.name,page:p.name,pageId:p.id}];
      put('Page',[p.id],`${p.name} [${p.id}]`,{name:p.name,hidden:p.hidden,width:p.width,height:p.height,filters:p.filters,fields:p.otherFields},pages);
      for(const v of p.visuals||[]) put('Visual',[p.id,v.id],`${p.name} [${p.id}] / ${v.title||v.type} [${v.id}]`,v,pages);
    }
  }
  return result;
}
function compareExtracts(before,after){
  const a=snapshot(before),b=snapshot(after), rows=[];
  for(const key of new Set([...a.keys(),...b.keys()])){
    const old=a.get(key),current=b.get(key);
    if(old?.value===current?.value) continue;
    const item=current||old;
    rows.push({kind:item.kind,item:item.label,change:!old?'Added':!current?'Removed':'Changed',before:old?.value||'',after:current?.value||'',
      pages:[...new Map([...(old?.pages||[]),...(current?.pages||[])].map(r=>[stable([r.report,r.pageId]),r])).values()]});
  }
  return rows.sort((a,b)=>a.kind.localeCompare(b.kind)||a.item.localeCompare(b.item));
}
function validateExtract(value){
  if(value?.schemaVersion && ![1,2].includes(value.schemaVersion)) throw new Error('Unsupported extract schema version: '+value.schemaVersion);
  if(!value||typeof value!=='object'||Array.isArray(value)||(!value.model&&!value.report)) throw new Error('Choose a JSON extract produced by --json.');
  if(value.model&&(!Array.isArray(value.model.tables)||!Array.isArray(value.model.measures))) throw new Error('The model definition is incomplete.');
  if(value.report&&(!Array.isArray(value.report.pages)||value.report.pages.some(p=>!p||typeof p.id!=='string'||!Array.isArray(p.visuals)))) throw new Error('The report pages are incomplete.');
  snapshot(value); // Reject malformed nested definitions before replacing an existing comparison.
  return value;
}
async function loadComparison(input){
  const file=input.files?.[0];if(!file) return;
  try{
    if(file.size>50*1024*1024) throw new Error('Choose an extract smaller than 50 MB.');
    const value=validateExtract(JSON.parse(await file.text()));
    comparison=value;comparisonName=file.name;comparisonError='';
  }catch(error){comparisonError='Could not compare this file: '+error.message;}
  switchTab('compare');
}
function comparisonRows(){return comparison?compareExtracts(comparison,DATA).filter(r=>pageScope==='*'||!r.pages.length||r.pages.some(inPageScope)):[];}
function rCompare(){
  const rows=comparisonRows();
  const mismatch=comparison&&(comparison.model?.name!==M?.name||comparison.report?.name!==R?.name||comparison.mode!==MODE);
  return `<h1>Compare extracts</h1><p class="sub">Choose an earlier JSON extract as the baseline. This document is the current extract. Files are read locally; no upload is made. Compares supported definitions including calculation groups, shared M expressions and detail rows, plus sources, DAX, filters, relationships, bookmarks and visual bindings. Data values and unsupported metadata are not compared. Shared-expression changes have unresolved page scope.</p>
    <label>Earlier extract (.json) <input type="file" accept=".json,application/json" onchange="loadComparison(this)"></label>
    <p role="status">${esc(comparisonError)}</p>${comparison?`<p>Baseline: <b>${esc(comparisonName)}</b> (${esc(comparison.generated||'date unknown')}) → Current: ${esc(DATA.generated)}</p>
    ${mismatch?'<div class="card">The report/model names or extraction modes differ. Changes may reflect different projects or missing input files; confirm that these extracts are comparable.</div>':''}
    ${!comparison.model?.expressions&&comparison.model?'<p class="mut">The baseline may omit shared expressions and other executable definitions; some additions may reflect improved extraction coverage.</p>':''}
    ${!comparison.columns?.tablePages?'<p class="mut">The baseline lacks page-level model usage; affected pages for removed model objects may be incomplete.</p>':''}
    <p>${rows.length} changes in this scope · <button class="xl" onclick="exportComparison()">Export changes by page</button></p>
    <table class="t"><thead><tr><th>Change</th><th>Object</th><th>Affected report pages</th><th>Definition</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.change)}</td><th>${esc(r.kind)}<br>${esc(r.item)}</th><td>${r.pages.filter(inPageScope).map(p=>esc(pageLabel(p))).join('<br>')||'No resolved page / model scope'}</td><td><details><summary>Before / after</summary><b>Before</b><pre class="code">${esc(r.before)||'Absent'}</pre><b>After</b><pre class="code">${esc(r.after)||'Absent'}</pre></details></td></tr>`).join('')||'<tr><td colspan="4">No definition changes detected in this scope.</td></tr>'}</tbody></table>`:''}`;
}
function exportComparison(){
  const rows=comparisonRows().flatMap(r=>(r.pages.length?r.pages.filter(inPageScope):[{report:R?.name||'Not supplied',page:'',pageId:''}]).map(p=>({...r,...p})));
  exportCsvFile(inventoryCsv(rows,[['change','Change'],['kind','Object type'],['item','Object'],['report','Report'],['page','Report page'],['pageId','Page ID'],['before','Before'],['after','After']]),'extract-changes-by-page.csv');
}
TABS.splice(2,0,
  {id:'matrix',label:'Usage matrix',group:'Start',avail:has.model&&has.report},
  {id:'impact',label:'Impact inspector',group:'Start',avail:has.model});
TABS.splice(TABS.findIndex(t=>t.id==='filters'),0,{id:'layout',label:'Page layout',group:'Report',avail:has.report});
TABS.push({id:'cleanup',label:'Cleanup review',group:'Quality',avail:has.model},
  {id:'compare',label:'Compare extracts',group:'Quality',avail:true,hidden:true});
Object.assign(RENDER,{matrix:rMatrix,impact:rImpact,layout:rLayout,cleanup:rCleanup,compare:rCompare});


/* Source-object lineage uses stable page IDs and the common CSV export path. */
const sourceObjectCsvFields=[['report','Report'],['page','Report page'],['pageId','Page ID'],
 ['pageScope','Page scope'],['pageUsage','Page usage'],['table','Model table'],['partition','Partition'],
 ['queryName','Query name'],['sourceType','Source type'],['server','Server / connection'],
 ['database','Database / service'],['schema','Schema'],['object','Source object'],
 ['status','Extraction status'],['notes','Review notes'],['evidence','Extraction evidence'],
 ['originalM','Original M code'],['sql','Extracted SQL'],['referencedM','Referenced M code']];
let visibleSourceObjects=[];
function sourceObjectRows(q='',status=''){
 q=q.trim().toLowerCase();
 return (DATA.sourceObjects||[]).filter(inPageScope).filter(r=>(!status||r.status===status)&&
  (!q||Object.values(r).join(' ').toLowerCase().includes(q)));
}
function rSourceObjects(){
 return `<h2>Report page → source object</h2>
 <p class="sub">One row per page, model-table partition and source object. Repeated references are deduplicated; unknown sources stay visible. Resolved means identified statically, not checked against a live database. Partial means some context or coverage remains uncertain.</p>
 <div class="filter-row"><input id="source-object-search" class="search" style="margin:0" aria-label="Search source objects" placeholder="Search report, source object, connection or code…" oninput="filterSourceObjects()">
 <select id="source-object-status" class="search" style="width:auto;margin:0" aria-label="Filter source extraction status" onchange="filterSourceObjects()"><option value="">All extraction statuses</option>${['Resolved','Partial','Unresolved','Not applicable'].map(x=>`<option>${x}</option>`).join('')}</select>
 <button class="chip" onclick="downloadSourceObjectsCsv(false)">Export source objects CSV (no code)</button>
 <button class="chip" onclick="downloadSourceObjectsCsv()">Export source objects CSV (with code)</button></div>
 <p id="source-object-count" class="mut" aria-live="polite"></p>
 <div class="column-scroll"><table class="t"><thead><tr><th>Report page</th><th>Model table / query</th><th>Source object</th><th>Status / original code</th></tr></thead><tbody id="source-object-rows"></tbody></table></div>`;
}
function filterSourceObjects(){
 visibleSourceObjects=sourceObjectRows(document.getElementById('source-object-search').value,document.getElementById('source-object-status').value);
 document.getElementById('source-object-count').textContent=`${visibleSourceObjects.length} source-object/page rows`;
 document.getElementById('source-object-rows').innerHTML=visibleSourceObjects.map(r=>`<tr>
 <td title="${esc(r.pageId)}">${esc(r.report)}<br>${esc(r.page)||esc(r.pageScope)}<div class="mut">${esc(r.pageUsage)}</div></td>
 <td>${esc(r.table)}<div class="mut">${esc(r.queryName)}</div></td>
 <td><b>${esc(r.object)||'No object resolved'}</b><div class="mut">${esc(r.sourceType)} · ${esc(r.server)||'server unresolved'}${r.database?' / '+esc(r.database):''}${r.schema?' / '+esc(r.schema):''}</div></td>
 <td><span class="badge ${r.status==='Resolved'?'b-direct':r.status==='Not applicable'?'b-other':'b-warn'}">${esc(r.status)}</span>
 <details><summary>View source code</summary><div class="body"><b>Extraction evidence</b><p>${esc(r.evidence)}</p><p>${listText(r.notes)}</p>
 <b>Original M code</b><pre class="code">${esc(r.originalM)||'No M expression for this partition.'}</pre>
 <b>Extracted SQL</b><pre class="code">${esc(r.sql)||'No resolved native SQL text.'}</pre>
 ${r.referencedM?`<b>Referenced M queries / parameters</b><pre class="code">${esc(r.referencedM)}</pre>`:''}</div></details></td></tr>`).join('')||'<tr><td colspan="4">No source objects match this selection.</td></tr>';
}
function downloadSourceObjectsCsv(includeCode=true){
 const fields=includeCode?sourceObjectCsvFields:sourceObjectCsvFields.filter(([key])=>!['originalM','sql','referencedM'].includes(key));
 exportCsvFile(inventoryCsv(visibleSourceObjects,fields),includeCode?'source-objects.csv':'source-objects-no-code.csv');
}

/* External identities only: never export raw code or free-form extraction evidence. */
const primarySourceCsvFields=[['report','Report'],['page','Report page'],['pageId','Page ID'],
 ['pageScope','Page scope'],['pageUsage','Page usage'],['sourceType','Source type'],
 ['server','Server / connection'],['database','Database / service'],['schema','Schema'],
 ['object','Source object'],['primaryQueries','Connection queries'],
 ['consumingQueries','Consuming model queries'],['tables','Model tables'],['status','Extraction status'],
 ['location','File / folder / URL'],['dependencyStatus','Dependency status'],['reportingStatus','Reporting usage'],
 ['usageConfidence','Usage confidence'],['runtimeStatus','Runtime / refresh status'],['usageEvidence','Usage evidence'],
 ['preparationEffects','Preparation effects'],['definitionQueries','Definition queries'],['removalAssessment','Removal assessment'],['storageModes','Configured storage modes']];
let visiblePrimarySources=[];
function rPrimarySources(){
 return `<p class="sub">External connections and source objects, traced through referenced queries. One row per report page and external input; multiple consumers are grouped. Connection queries open the external connection. Consuming model queries may use it directly, reference another query, or do both.</p>
 <p class="mut">Includes SQL Server, Oracle, Teradata, ODBC, SharePoint files/lists, local/network files, folders, web/API, OData and Azure storage. Other connectors remain unresolved; this is not the complete Power BI connector catalogue. Shared queries with no resolved model consumer appear under “No specific page”.</p>
 <div class="filter-row"><input id="primary-source-search" class="search" style="margin:0" aria-label="Search primary sources" placeholder="Search connection, database, object or query…" oninput="filterPrimarySources()">
 <select id="primary-source-usage" class="search" style="width:auto;margin:0" aria-label="Filter primary sources by reporting usage" onchange="filterPrimarySources()"><option value="">All reporting usage</option>${['Potential reporting dependency','Possible model dependency','Report scope only','No reporting usage found','No model consumer found','Usage unresolved'].map(x=>`<option>${x}</option>`).join('')}</select>
 <button class="chip" onclick="downloadPrimarySourcesCsv()">Export primary sources CSV</button></div>
 <p class="mut">Reporting usage is separate from source identification. Page usage describes the downstream model table; an external input’s contribution to displayed values is not proven. Merges and filters may affect results without supplying visible columns. Load/refresh execution is not observed. No reporting usage found is not a deletion verdict.</p>
 <p class="mut">Metadata only. Export replaces tabs and line breaks with spaces so each record occupies one physical line. No M code, SQL, referenced-query code or raw extraction notes are included. Usage evidence is a short assessment, not source code.</p>
 <p id="primary-source-count" class="mut" aria-live="polite"></p>
 <div id="primary-source-coverage"></div>
 <div class="column-scroll"><table class="t"><thead><tr><th>Report / page</th><th>Type</th><th>Server / connection</th><th>Database / service</th><th>Schema</th><th>Source object / file / URL</th><th>Connection queries</th><th>Consuming model queries / tables</th><th>Source identification</th><th>Reporting usage / dependency</th></tr></thead><tbody id="primary-source-rows"></tbody></table></div>`;
}
function filterPrimarySources(){
 const q=document.getElementById('primary-source-search').value.trim().toLowerCase();
 const data=DATA.primarySources||{rows:[],unresolved:[]};
 const usage=document.getElementById('primary-source-usage').value;
 visiblePrimarySources=data.rows.filter(inPageScope).filter(r=>(!usage||r.reportingStatus===usage)&&(!q||primarySourceCsvFields.some(([key])=>String(r[key]??'').toLowerCase().includes(q))));
 const unresolved=data.unresolved.filter(inPageScope);
 document.getElementById('primary-source-count').textContent=`${visiblePrimarySources.length} external-input/page rows (including unresolved coverage)`;
 document.getElementById('primary-source-coverage').innerHTML=unresolved.length?`<details><summary>${unresolved.length} query/page entries have unresolved source coverage</summary><p>These entries are retained as unresolved rows in the view and export. Known sources from partially resolved queries also remain listed. This coverage list follows the selected page, independently of search.</p><ul>${unresolved.map(r=>`<li>${esc(r.queryName)} — ${esc(r.page)||esc(r.pageScope)}</li>`).join('')}</ul></details>`:'';
 document.getElementById('primary-source-rows').innerHTML=visiblePrimarySources.map(r=>`<tr>
 <td title="${esc(r.pageId)}">${esc(r.report)}<br>${esc(r.page)||esc(r.pageScope)}<div class="mut">${listText(r.pageUsage)}</div></td>
 <td>${esc(r.sourceType)}</td><td>${esc(r.server)||'Unresolved / not supplied'}</td><td>${esc(r.database)||'Unresolved / not supplied'}</td>
 <td>${esc(r.schema)||'—'}</td><td>${esc(r.object)||'Object unresolved'}<div class="mut">${esc(r.location)}</div></td><td>${listText(r.primaryQueries)}</td>
 <td>${listText(r.consumingQueries)}<div class="mut">${listText(r.tables)}</div></td><td>${esc(r.status)}</td>
 <td><b>${esc(r.reportingStatus)}</b><div>${listText(r.dependencyStatus)}</div><details><summary>Usage evidence</summary><p>${listText(r.usageEvidence)}</p><p>${listText(r.preparationEffects)}</p><p>Configured storage modes: ${listText(r.storageModes)}</p><p>Confidence: ${esc(r.usageConfidence)}. ${esc(r.runtimeStatus)}.</p></details></td></tr>`).join('')||'<tr><td colspan="10">No identified primary sources match this selection. Check unresolved coverage above.</td></tr>';
}
function downloadPrimarySourcesCsv(){
 const singleLine=value=>(Array.isArray(value)?value.join('; '):String(value??'')).replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]+/g,' ');
 const rows=visiblePrimarySources.map(row=>Object.fromEntries(primarySourceCsvFields.map(([key])=>[key,singleLine(row[key])])));
 exportCsvFile(inventoryCsv(rows,primarySourceCsvFields),'primary-sources.csv');
}
/* One row per external source (B3). Pages are tags; detail opens in the inspector. */
const USAGE_ORDER=['Usage unresolved','Potential reporting dependency','Possible model dependency','Report scope only','No reporting usage found','No model consumer found'];
const USAGE_BADGE={'Potential reporting dependency':'b-direct','Possible model dependency':'b-poss','Report scope only':'b-viam','No reporting usage found':'b-none','No model consumer found':'b-none','Usage unresolved':'b-warn'};
const FILE_SOURCE_TYPES=new Set(['SharePoint file','Excel workbook','CSV file','File','Azure Blob Storage','Azure Data Lake']);
const uniq=a=>[...new Set(a.filter(Boolean))].sort();
function sourceName(g){
  if(FILE_SOURCE_TYPES.has(g.sourceType)) return g.object||String(g.location||'').split(/[\/\\]/).filter(Boolean).pop()||'Unresolved file';
  // Same rule as source_labels.source_name: a dataflow is known by its entity.
  if(/dataflow$/.test(g.sourceType)) return g.object||g.database||'Unresolved entity';
  // A SharePoint list picked by ID: its title is not in the report.
  if(g.sourceType==='SharePoint list'&&/^\{?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\}?$/i.test(g.object||''))
    return `List ID ${g.object.replace(/[{}]/g,'').slice(0,8)}… (title not in report)`;
  const obj=g.schema&&g.object?`${g.schema}.${g.object}`:g.object;
  return [g.database,obj].filter(Boolean).join(' · ')||g.server||g.location||'Unresolved';
}
function sourceGroups(){
  const groups=new Map();
  for(const r of DATA.primarySources?.rows||[]){
    const key=JSON.stringify([r.sourceType,r.server,r.database,r.schema,r.object,r.location]);
    if(!groups.has(key)) groups.set(key,{key,sourceType:r.sourceType,server:r.server,database:r.database,schema:r.schema,object:r.object,location:r.location,rows:[]});
    groups.get(key).rows.push(r);
  }
  return [...groups.values()].map(g=>{
    const statuses=g.rows.map(r=>r.status), usages=g.rows.map(r=>r.reportingStatus);
    return {...g,name:sourceName(g),
      tables:uniq(g.rows.flatMap(r=>r.tables)),
      primaryQueries:uniq(g.rows.flatMap(r=>r.primaryQueries)),
      consumingQueries:uniq(g.rows.flatMap(r=>r.consumingQueries)),
      pages:[...new Map(g.rows.filter(r=>r.pageId).map(r=>[r.pageId,r])).values()],
      status:statuses.includes('Unresolved')?'Unresolved':statuses.includes('Partial')?'Partial':'Resolved',
      reportingStatus:USAGE_ORDER.find(u=>usages.includes(u))||usages[0]||''};
  }).sort((a,b)=>a.sourceType.localeCompare(b.sourceType)||a.name.localeCompare(b.name));
}
let visibleSourceGroups=[];
function rSourceList(){
  if(!(DATA.primarySources?.rows||[]).length) return '<div class="empty"><b>No external sources identified.</b></div>';
  return `<div class="filter-row"><input id="source-search" class="search" style="margin:0" aria-label="Search sources" placeholder="Search source, server, table or query…" oninput="filterSourceList()"></div>
  <p id="source-count" class="mut" aria-live="polite"></p>
  <table class="t source-list"><thead><tr><th>Source</th><th>Model tables</th><th>Report pages</th><th>Reporting usage</th><th>Identification</th></tr></thead><tbody id="source-list-rows"></tbody></table>`;
}
function filterSourceList(){
  const body=document.getElementById('source-list-rows');if(!body) return;
  const q=(document.getElementById('source-search')?.value||'').trim().toLowerCase();
  visibleSourceGroups=sourceGroups().filter(g=>g.rows.some(inPageScope)).filter(g=>!q||[g.sourceType,g.name,g.server,g.location,...g.tables,...g.primaryQueries,...g.consumingQueries].join(' ').toLowerCase().includes(q));
  document.getElementById('source-count').textContent=`${visibleSourceGroups.length} sources`;
  body.innerHTML=visibleSourceGroups.map(g=>`<tr><th><button class="xl" onclick="${action('inspectSource',g.key)}">${esc(g.sourceType)} · ${esc(g.name)}</button>
    <div class="mut">${esc(g.server||g.location||'')}</div></th>
    <td>${g.tables.map(tblLink).join(', ')||'<span class="mut">No model consumer</span>'}</td>
    <td><div class="pill-list">${g.pages.map(p=>`<span class="tag-page">${esc(p.page)}</span>`).join('')||'<span class="mut">No page usage</span>'}</div></td>
    <td><span class="badge ${USAGE_BADGE[g.reportingStatus]||'b-other'}">${esc(g.reportingStatus)}</span></td>
    <td><span class="badge ${g.status==='Resolved'?'b-direct':'b-warn'}">${esc(g.status)}</span></td></tr>`).join('')||'<tr><td colspan="5">No sources match this selection.</td></tr>';
}
function inspectSource(key){
  const g=sourceGroups().find(x=>x.key===key);if(!g) return;
  const queries=uniq([...g.primaryQueries,...g.consumingQueries]);
  const code=(DATA.sourceQueries||[]).filter(q=>queries.includes(q.queryName));
  const sql=uniq((DATA.sourceObjects||[]).filter(o=>queries.includes(o.queryName)&&o.sql&&(!g.object||o.object===g.object||o.object===`${g.schema}.${g.object}`)).map(o=>o.sql));
  const field=(label,value)=>value?`<tr><td class="mut">${label}</td><td>${esc(value)}</td></tr>`:'';
  openInspector(`${g.sourceType} · ${g.name}`,`<table class="t"><tbody>${field('Type',g.sourceType)}${field('Server / connection',g.server)}${field('Database / service',g.database)}${field('Schema',g.schema)}${field('Object',g.object)}${field('File / folder / URL',g.location)}</tbody></table>
    <p><span class="badge ${USAGE_BADGE[g.reportingStatus]||'b-other'}">${esc(g.reportingStatus)}</span> <span class="badge ${g.status==='Resolved'?'b-direct':'b-warn'}">${esc(g.status)}</span></p>
    <p class="mut">Resolved means identified statically, not checked against a live source. Reporting usage describes the downstream model table; this input's contribution to displayed values is not proven.</p>
    <h3>Report pages</h3><p>${g.rows.filter(inPageScope).map(r=>`${esc(pageLabel(r))}: ${esc(r.reportingStatus)}`).join('<br>')||'None'}</p>
    <h3>Model tables</h3><p>${g.tables.map(tblLink).join(', ')||'None'}</p>
    <h3>Queries</h3><p>Connection: ${listText(g.primaryQueries)}</p><p>Consuming: ${listText(g.consumingQueries)}</p>
    <h3>Evidence</h3><p>${listText(uniq(g.rows.flatMap(r=>r.usageEvidence)))}</p><p>${listText(uniq(g.rows.flatMap(r=>r.preparationEffects)))}</p>
    <p class="mut">Configured storage modes: ${listText(uniq(g.rows.flatMap(r=>r.storageModes)))}</p>
    <h3>Code</h3>${code.map(q=>`<details><summary>M · ${esc(q.queryName)}</summary><pre class="code">${esc(q.mCode)}</pre></details>`).join('')||'<p>No M code found.</p>'}
    ${sql.map(s=>`<details><summary>Extracted SQL</summary><pre class="code">${esc(s)}</pre></details>`).join('')}`);
}
