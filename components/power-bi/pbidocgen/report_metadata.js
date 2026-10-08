/* Portable user-maintained documentation. Never stored only in browser storage. */
function cleanDocumentation(value){
 const fields=['sourceType','server','database','username','authentication','connectionName'];
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(k=>!['reportLocation','folder','connections'].includes(k))) throw Error('Unsupported documentation fields');
 const out={reportLocation:value.reportLocation||'',folder:value.folder||'',connections:value.connections||[]};
 if(typeof out.reportLocation!=='string'||typeof out.folder!=='string'||!Array.isArray(out.connections)) throw Error('Invalid documentation metadata');
 out.connections=out.connections.map(c=>{
  if(!c||typeof c!=='object'||Array.isArray(c)||Object.keys(c).some(k=>!fields.includes(k))||Object.values(c).some(v=>typeof v!=='string')) throw Error('Connections accept identity and username fields only; no passwords or tokens');
  return Object.fromEntries(fields.map(k=>[k,c[k]||'']));
 });
 return out;
}
let documentation=cleanDocumentation(JSON.parse(document.getElementById('pbi-documentation-metadata')?.textContent||'{}'));
// Source types were renamed (File -> CSV file/Excel workbook, Web / API -> SharePoint file).
// Match saved details by family so usernames survive regeneration.
const SOURCE_FAMILY={'File':'file','CSV file':'file','Excel workbook':'file','Web':'web','Web / API':'web','SharePoint file':'web','SharePoint':'sharepoint','SharePoint files':'sharepoint'};
const connectionIdentity=c=>JSON.stringify([SOURCE_FAMILY[c.sourceType]||c.sourceType||'',c.server||'',c.database||'']);
for(const source of DATA.sourceObjects||[]){
 if(source.status==='Not applicable') continue;
 const saved=documentation.connections.find(c=>connectionIdentity(c)===connectionIdentity(source));
 if(saved){ if(source.sourceType) saved.sourceType=source.sourceType; }
 else documentation.connections.push(Object.fromEntries(['sourceType','server','database','username','authentication','connectionName'].map(k=>[k,source[k]||''])));
}
// Snapshot of what the file already holds, so the save bar can show unsaved edits.
let savedDocumentation=JSON.stringify(cleanDocumentation(documentation));
const CONNECTION_FIELDS={connectionName:'Connection name',sourceType:'Source type',server:'Server / location',database:'Database / service'};
function documentationInput(label,id,value,cls=''){return `<label class="doc-field ${cls}"><span>${esc(label)}</span><input class="search" id="${id}" value="${esc(value)}" oninput="captureDocumentation()"></label>`;}
function unsavedCount(){
 const now=cleanDocumentation(documentation),then=JSON.parse(savedDocumentation);
 let n=(now.reportLocation!==then.reportLocation)+(now.folder!==then.folder);
 const len=Math.max(now.connections.length,then.connections.length);
 for(let i=0;i<len;i++) if(JSON.stringify(now.connections[i])!==JSON.stringify(then.connections[i])) n++;
 return n;
}
function updateSaveBar(){
 const n=unsavedCount(),bar=document.getElementById('doc-savebar'),status=document.getElementById('doc-status');
 if(!bar||!status) return;
 bar.classList[n>0?'add':'remove']('dirty');
 status.textContent=n?`${n} unsaved ${n===1?'change':'changes'}. Download the updated HTML and replace the original file to keep them.`:'No unsaved changes. After downloading, replace the original file, then refresh the home page catalogue.';
}
function captureDocumentation(){
 documentation.reportLocation=document.getElementById('doc-location').value;
 documentation.folder=document.getElementById('doc-folder').value;
 documentation.connections.forEach((c,i)=>Object.keys(c).forEach(k=>{c[k]=document.getElementById(`doc-${i}-${k}`).value;}));
 updateSaveBar();
}
function addDocumentationConnection(){captureDocumentation();documentation.connections.push({sourceType:'',server:'',database:'',username:'',authentication:'',connectionName:''});documentationOpen.add(documentation.connections.length-1);switchTab('report-details');}
function removeDocumentationConnection(i){captureDocumentation();documentation.connections.splice(i,1);documentationOpen.clear();switchTab('report-details');}
const documentationOpen=new Set();
function toggleConnectionEdit(i){
 documentationOpen.has(i)?documentationOpen.delete(i):documentationOpen.add(i);
 const row=document.getElementById(`doc-edit-${i}`),btn=document.getElementById(`doc-edit-${i}-btn`);
 if(row) row.hidden=!documentationOpen.has(i);
 if(btn) btn.setAttribute('aria-expanded',String(documentationOpen.has(i)));
}
const FILE_CONNECTION_TYPES=new Set(['SharePoint file','Excel workbook','CSV file','File']);
function connectionTitle(c,i){
 const file=FILE_CONNECTION_TYPES.has(c.sourceType)&&c.server?c.server.split(/[\\/]/).filter(Boolean).pop():'';
 const name=c.connectionName||file||[c.server,c.database].filter(Boolean).join(' / ')||'New connection '+(i+1);
 const where=file||c.connectionName?[c.server,c.database].filter(Boolean).join(' / '):'';
 return `${c.sourceType?esc(c.sourceType)+' · ':''}${esc(name)}${where?`<div class="mut">${esc(where)}</div>`:''}`;
}
// Published copies (DATA.published, set by the library) are read-only: the library owns metadata edits.
function rDocumentationReadOnly(){
 const rows=documentation.connections.map((c,i)=>`<tr><th>${connectionTitle(c,i)}</th><td>${esc(c.username||'')}</td><td>${esc(c.authentication||'')}</td></tr>`).join('');
 return `<h1>Documentation details</h1><p class="sub doc-readonly" role="note">This is a published, read-only copy. Edit document metadata in the library, or regenerate the documentation from the source.</p>
<div class="card doc-grid"><p><strong>Original report URL or file path:</strong> ${esc(documentation.reportLocation||'Not recorded')}</p><p><strong>Catalogue folder:</strong> ${esc(documentation.folder||'Not recorded')}</p></div>
<h2>Connections and account references</h2>
<table class="t doc-connections"><thead><tr><th>Connection</th><th>Username / service account</th><th>Authentication</th></tr></thead><tbody>${rows||'<tr><td colspan="3">No connections recorded.</td></tr>'}</tbody></table>`;}
function rDocumentation(){
 if(DATA.published) return rDocumentationReadOnly();
 const rows=documentation.connections.map((c,i)=>`<tr>
  <th>${connectionTitle(c,i)}</th>
  <td>${documentationInput('Username / service account',`doc-${i}-username`,c.username,'compact')}</td>
  <td>${documentationInput('Authentication (reference only)',`doc-${i}-authentication`,c.authentication,'compact')}</td>
  <td class="doc-actions"><button class="chip" id="doc-edit-${i}-btn" aria-expanded="${documentationOpen.has(i)}" aria-controls="doc-edit-${i}" onclick="toggleConnectionEdit(${i})">Edit</button></td></tr>
  <tr class="doc-edit-row" id="doc-edit-${i}"${documentationOpen.has(i)?'':' hidden'}><td colspan="4"><div class="doc-edit">${Object.entries(CONNECTION_FIELDS).map(([k,label])=>documentationInput(label,`doc-${i}-${k}`,c[k])).join('')}
   <div><button class="chip" onclick="removeDocumentationConnection(${i})">Remove reference</button></div></div></td></tr>`).join('');
 return `<h1>Documentation details</h1><p class="sub">Keep the original report location and connection account references with this document. Enter usernames or service account names only; never passwords, tokens or secret-bearing URLs.</p>
<div class="card doc-grid">${documentationInput('Original report URL or file path','doc-location',documentation.reportLocation)}
${documentationInput('Catalogue folder (optional, e.g. Finance / Monthly)','doc-folder',documentation.folder)}
<p class="mut">The home page groups by this folder, or by the parent of the report location. Without either, it lists the document under Ungrouped.</p></div>
<h2>Connections and account references</h2><p class="mut">Detected connections are starting points. Account details are entered manually; the generator does not retrieve credentials from Power BI. Use Edit to change a connection's name, type, server or database.</p>
<table class="t doc-connections"><thead><tr><th>Connection</th><th>Username / service account</th><th>Authentication</th><th></th></tr></thead><tbody>${rows||'<tr><td colspan="4">No connections recorded.</td></tr>'}</tbody></table>
<p><button class="chip" onclick="addDocumentationConnection()">Add connection reference</button></p>
<div id="doc-savebar" class="doc-savebar"><p id="doc-status" role="status"></p><button class="primary-btn" onclick="saveDocumentationHtml()">Download updated HTML</button></div>`;}
function saveDocumentationHtml(){
 if(DATA.published) return;
 captureDocumentation();
 const clone=document.documentElement.cloneNode(true);
 clone.querySelector('#pbi-documentation-metadata').textContent=JSON.stringify(cleanDocumentation(documentation)).replace(/</g,'\\u003c').replace(/>/g,'\\u003e').replace(/&/g,'\\u0026');
 clone.querySelector('#main').innerHTML='';clone.querySelector('#nav').innerHTML='';
 const blob=new Blob(['<!DOCTYPE html>\n'+clone.outerHTML],{type:'text/html;charset=utf-8'});
 const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;
 a.download=DATA.documentationFilename||'report.html';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 savedDocumentation=JSON.stringify(cleanDocumentation(documentation));updateSaveBar();
 document.getElementById('doc-status').textContent='Downloaded. Replace the original HTML with the downloaded file to keep these details, then refresh the home page catalogue.';
}
TABS.push({id:'report-details',label:'Documentation details',group:'Documentation',avail:true});
RENDER['report-details']=rDocumentation;
