// View checks in a real browser, for any document: the Model view relationship surface (Change 7) and the
// Report view pages (Change 4, Report view).
// Shared by the fixture checks (browser_relationships.cjs) and the sample checks (scripts/check_sample_html.cjs).
// Returns a list of problems; empty when every counted table is a box, no two boxes overlap, every line starts
// and ends on the edge of its own two tables, and selecting each table presses that box alone and fills the panel.
async function surfaceProblems(page){
 return page.evaluate(()=>{
  const problems=[];
  if(!has.model) return problems;
  switchTab('rels',false,true);
  const tables=countedTables(),relationships=countedRelationships();
  const svg=document.querySelector('#rel-canvas svg');
  if(!relationships.length){ if(svg) problems.push('a diagram is drawn for a model without relationships'); return problems; }
  if(!svg) return ['no diagram'];
  if(/NaN|Infinity|undefined/.test(svg.outerHTML)) problems.push('invalid coordinates');
  const [,,W,H]=svg.getAttribute('viewBox').split(' ').map(Number);
  const boxes=[...svg.querySelectorAll('.rel-box')].map(g=>{const r=g.querySelector('rect');
   return {name:g.dataset.table,x:+r.getAttribute('x'),y:+r.getAttribute('y'),w:+r.getAttribute('width'),h:+r.getAttribute('height')};});
  if(boxes.length!==tables.length) problems.push(`${boxes.length} boxes for ${tables.length} tables`);
  const at=new Map(boxes.map(b=>[b.name,b]));
  for(const t of tables) if(!at.has(t.name)) problems.push('no box for '+t.name);
  for(const b of boxes) if(b.x<0||b.y<0||b.x+b.w>W||b.y+b.h>H) problems.push(b.name+' is outside the picture');
  for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){const a=boxes[i],b=boxes[j];
   if(!(a.x+a.w<=b.x||b.x+b.w<=a.x||a.y+a.h<=b.y||b.y+b.h<=a.y)) problems.push(a.name+' overlaps '+b.name);}
  const edges=[...svg.querySelectorAll('.rel-edge')];
  if(edges.length!==relationships.length) problems.push(`${edges.length} lines for ${relationships.length} relationships`);
  for(const g of edges){
   const r=relationships[+g.dataset.rel],d=g.querySelector('.rel-line').getAttribute('d');
   const nums=d.match(/-?[\d.]+/g).map(Number),[x1,y1]=nums,[x2,y2]=nums.slice(-2);
   const on=(b,x,y)=>b&&(Math.abs(x-b.x)<.2||Math.abs(x-b.x-b.w)<.2)&&y>b.y&&y<b.y+b.h;
   if(!on(at.get(r.fromTable),x1,y1)||!on(at.get(r.toTable),x2,y2)) problems.push(`${r.fromTable} → ${r.toTable} does not join its tables`);
  }
  for(const t of tables.slice(0,60)){
   selectRelTable(t.name);
   const pressed=[...document.querySelectorAll('#rel-canvas .rel-box[aria-pressed="true"]')].map(g=>g.dataset.table);
   if(pressed.length!==1||pressed[0]!==t.name) problems.push('selecting '+t.name+' presses '+pressed);
   if(document.querySelector('#rel-panel h2')?.textContent!==t.name) problems.push('the panel does not show '+t.name);
  }
  clearRelSelection();
  return problems;
 });
}
// Report view › Pages, for any document: every page opens on its own, in report order, with every placed visual
// drawn and every visual listed, and says what kind of page it is (or that the type is not recorded).
async function pagesProblems(page){
 return page.evaluate(()=>{
  const problems=[];
  if(!has.report||!R.pages.length) return problems;
  switchTab('pages',false,true);
  const strip=[...document.querySelectorAll('#page-strip .page-tab')].map(b=>b.dataset.page);
  if(strip.join('\u0001')!==R.pages.map(p=>p.id).join('\u0001')) problems.push('the page list is not every page in report order');
  for(const p of R.pages){
   showReportPage(p.id);
   if(!document.getElementById('pg-'+pageKey(p.id))) { problems.push('page '+p.id+' does not open'); continue; }
   const placed=layoutGeometry(p).placed.length;
   if(document.querySelectorAll('#page-surface .visual-box').length!==placed) problems.push(p.id+': '+placed+' placed visuals, '+document.querySelectorAll('#page-surface .visual-box').length+' drawn');
   const listed=document.querySelectorAll('#page-visuals tbody tr[id]').length+document.querySelectorAll('#page-decorative [id^="vis-"]').length;
   if(listed!==p.visuals.length) problems.push(p.id+': '+p.visuals.length+' visuals, '+listed+' listed');
   const facts=document.querySelector('.page-facts')?.textContent||'';
   if(!facts.startsWith(pageType(p).label)) problems.push(p.id+': the page type is not stated');
   const v=p.visuals[0];
   if(v){ pickVisual(p.id,v.id,{reveal:false}); if(!document.getElementById('visual-clear')) problems.push(p.id+': selecting '+v.id+' does not fill the panel'); }
  }
  return problems;
 });
}
module.exports={surfaceProblems,pagesProblems};
