const $=id=>document.getElementById(id);
const names={missing:'Missing settlement',amount:'Amount mismatch',currency:'Currency mismatch',duplicate:'Duplicate installment',orphan:'Unmapped partner record'};
const state={currency:'SGD',page:1,limit:10,total:0,summary:null,detail:null,view:'overview',activeButton:null};
const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number=n=>new Intl.NumberFormat('en-SG').format(n);
const token=()=>`demo-${$('operator').value}-token`;
const amount=(v,cur)=>v==null?'—':new Intl.NumberFormat('en-SG',{minimumFractionDigits:cur==='JPY'?0:2,maximumFractionDigits:cur==='JPY'?0:2}).format(Number(v)/(cur==='JPY'?1:100));
function notify(text,error=false){$('notice').hidden=false;$('notice').textContent=text;$('notice').classList.toggle('error',error);}
async function api(path,options={}){
  const res=await fetch(path,{...options,headers:{Authorization:`Bearer ${token()}`,...options.headers}});
  if(!res.ok){let message;try{const data=await res.json();message=typeof data.detail==='string'?data.detail:JSON.stringify(data.detail)}catch{message=`Request failed (${res.status})`;}throw new Error(message);}
  return res.json();
}
const params=()=>new URLSearchParams({currency:state.currency,reason:$('reason').value,status:$('status').value,page:state.page,limit:state.limit});
function renderSummary(s){
  state.summary=s;$('asof').value=s.run.as_of;
  $('match-rate').textContent=s.match_rate.toFixed(2)+'%';$('match-track').style.width=s.match_rate+'%';
  $('matched-detail').textContent=`${number(s.matched)} matched / ${number(s.eligible_ledger_entries)} eligible ledger entries`;
  $('exposure').textContent=s.flagged_ledger_display;$('amount-currency').textContent=s.currency;
  $('exception-count').textContent=number(s.exception_count);$('nav-count').textContent=number(s.exception_count);
  $('case-detail').textContent=`${number(s.case_statuses.open||0)} open · ${number(s.case_statuses.investigating||0)} investigating · ${number(s.case_statuses.resolved||0)} resolved`;
  $('pending-count').textContent=number(s.counts.pending||0);$('aging-currency').textContent=s.currency;
  $('last-run').textContent=`Snapshot ${s.run.id.slice(0,8)} · ${Number(s.run.duration_ms).toFixed(0)} ms local run`;
  const max=Math.max(...Object.keys(names).map(k=>s.counts[k]||0),1);
  $('reason-chart').innerHTML=Object.entries(names).map(([k,label])=>`<div class="bar-row" data-reason="${k}"><span>${label}</span><div class="bar-track"><div style="width:${(s.counts[k]||0)/max*100}%"></div></div><b>${s.counts[k]||0}</b></div>`).join('');
  const buckets=['0-2 days','3-7 days','8-30 days','31+ days'];const aging=new Map(s.aging.map(r=>[r.bucket,r]));
  const peak=Math.max(...s.aging.map(r=>Number(r.amount_minor)),1);
  $('aging-chart').innerHTML=buckets.map(b=>{const r=aging.get(b);return `<div class="aging-bar"><strong>${r?esc(r.display):'0'}</strong><div class="column" style="height:${r?Number(r.amount_minor)/peak*98:0}px"></div><span>${b}</span></div>`;}).join('');
  $('controls').innerHTML=s.controls.map(c=>`<div class="control"><span class="check">${c.passed?'✓':'!'}</span><div>${esc(c.name)}<small>${esc(c.detail)}</small></div></div>`).join('');
  $('source-list').innerHTML=s.sources.map(r=>`<div class="source-row"><div><b>${r.source==='ledger'?'Internal ledger':'Partner settlement'} CSV</b><small>${number(r.row_count)} rows · ${new Date(r.imported_at).toLocaleString('en-SG')}</small><code>SHA-256 ${esc(r.sha256)}</code></div><span class="subtle-pill">Accepted atomically</span></div>`).join('');
  $('history-list').innerHTML=s.history.map(r=>`<div class="history-row"><div><b>${r.id.slice(0,8)}</b><small>As of ${r.as_of} · ${new Date(r.created_at).toLocaleString('en-SG')}</small></div><div>${number(Object.values(r.counts).reduce((a,b)=>a+b,0))} classified items<small>${Number(r.duration_ms).toFixed(1)} ms · local workstation</small></div></div>`).join('');
}
async function loadQueue(){
  const q=await api('/api/cases?'+params());state.total=q.total;
  $('queue-total').textContent=number(q.total);
  $('queue-body').innerHTML=q.rows.length?q.rows.map(r=>`<tr><td>${esc(r.item_key)}<small>${esc(r.payment_id||'No internal ledger event')}</small></td><td><span class="reason-badge ${r.reason}">${names[r.reason]}</span></td><td>${amount(r.ledger_net,r.ledger_currency)}<small>${esc(r.ledger_currency)}</small></td><td>${amount(r.partner_net,r.partner_currency)}<small>${esc(r.partner_currency)}</small></td><td>${amount(r.delta_minor,r.ledger_currency)}</td><td><span class="status-badge ${r.status}">${r.status[0].toUpperCase()+r.status.slice(1)}</span></td><td><button class="inspect" data-case="${r.id}" aria-label="Inspect ${esc(r.item_key)}">↗</button></td></tr>`).join(''):'<tr><td colspan="7" class="empty">No cases match these filters.</td></tr>';
  $('pagination-label').textContent=q.total?`Showing ${(state.page-1)*state.limit+1}–${Math.min(state.page*state.limit,q.total)} of ${number(q.total)} cases`:'No cases';
  $('previous').disabled=state.page<=1;$('next').disabled=state.page*state.limit>=q.total;
}
async function refresh(){try{const s=await api('/api/summary?currency='+state.currency);renderSummary(s);await loadQueue();}catch(e){notify(e.message,true);}}
function setView(view){
  state.view=view;document.querySelectorAll('.nav').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
  $('overview').hidden=view!=='overview';$('sources').hidden=view!=='sources';$('exceptions').hidden=true;$('queue-panel').hidden=view==='sources';
  const labels={overview:['Reconciliation overview','Overview','Find the differences. Follow the evidence. Keep every decision traceable.'],exceptions:['Exception queue','Exception queue','Acknowledge, investigate and review the evidence behind every exception.'],sources:['Sources & runs','Sources & runs','Trace input files and preserve a reproducible history of each snapshot.']};
  $('page-title').textContent=labels[view][0];$('breadcrumb-view').textContent=labels[view][1];$('page-description').textContent=labels[view][2];
}
function recordCard(title,row){
  if(!row)return `<div class="record-card"><h3>${title}</h3><p class="fine">No matching source record.</p></div>`;
  const fields=['row_id','event_id','kind','installment','currency','gross_minor','fee_minor','net_minor','expected_on','settled_on','payout_id','batch_id'];
  return `<div class="record-card"><h3>${title}</h3><dl>${fields.filter(k=>row[k]!=null).map(k=>`<dt>${k.replaceAll('_',' ')}</dt><dd>${esc(row[k])}</dd>`).join('')}</dl></div>`;
}
async function openCase(id){
  try{
    const d=await api('/api/cases/'+id);state.detail=d;const r=d.case;
    $('drawer-title').textContent=r.item_key;
    const transitions=r.status==='open'?[['investigating','Start investigation']]:r.status==='investigating'?[['resolved','Resolve after review']]:[['investigating','Reopen investigation']];
    $('drawer-content').innerHTML=`<div class="detail-banner"><span class="reason-badge ${r.reason}">${names[r.reason]}</span> <span class="status-badge ${r.status}">${r.status}</span><p>Case version ${r.version}. Resolving a case records review; it does not change the financial reconciliation.</p></div>${recordCard('Internal ledger',d.ledger)}${d.partner.map((p,i)=>recordCard('Partner record '+(i+1),p)).join('')||recordCard('Partner settlement',null)}<section class="drawer-section"><h3>Record a decision</h3><p>Resolution requires the Reviewer demo role. Stale edits are rejected.</p><label for="transition">Next status</label><select id="transition">${transitions.map(([value,label])=>`<option value="${value}">${label}</option>`).join('')}</select><label for="decision-note">Investigation note</label><textarea id="decision-note" placeholder="Describe the evidence and the next action (at least 8 characters)."></textarea><button id="save-decision" class="button primary">Save decision</button><p id="decision-result" role="status"></p></section><section class="drawer-section"><h3>Audit trail</h3>${d.audit.length?d.audit.map(a=>`<div class="audit-row"><b>${esc(a.from_status)} → ${esc(a.to_status)}</b><p>${esc(a.note)}</p><small>${esc(a.actor)} · v${a.version} · ${new Date(a.created_at).toLocaleString('en-SG')}</small></div>`).join(''):'<p>No decisions recorded yet.</p>'}</section>`;
    $('drawer').hidden=false;$('scrim').hidden=false;document.body.style.overflow='hidden';$('close-drawer').focus();
    $('save-decision').onclick=saveDecision;
  }catch(e){notify(e.message,true);}
}
async function saveDecision(){
  const button=$('save-decision');const note=$('decision-note').value.trim();
  if(note.length<8){$('decision-result').textContent='Please add a note of at least 8 characters.';return;}
  button.disabled=true;const d=state.detail;
  try{
    await api('/api/cases/'+d.case.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_version:d.case.version,status:$('transition').value,note})});
    await openCase(d.case.id);await refresh();
  }catch(e){$('decision-result').textContent=e.message;button.disabled=false;}
}
function closeDrawer(){$('drawer').hidden=true;$('scrim').hidden=true;document.body.style.overflow='';state.activeButton?.focus();}
$('queue-body').addEventListener('click',e=>{const b=e.target.closest('[data-case]');if(b){state.activeButton=b;openCase(b.dataset.case);}});
$('scrim').onclick=closeDrawer;$('close-drawer').onclick=closeDrawer;
document.addEventListener('keydown',e=>{
  if($('drawer').hidden)return;
  if(e.key==='Escape'){closeDrawer();return;}
  if(e.key==='Tab'){
    const focusable=[...$('drawer').querySelectorAll('button,select,textarea')].filter(x=>!x.disabled);
    if(e.shiftKey&&document.activeElement===focusable[0]){e.preventDefault();focusable.at(-1).focus();}
    else if(!e.shiftKey&&document.activeElement===focusable.at(-1)){e.preventDefault();focusable[0].focus();}
  }
});
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>setView(b.dataset.view));
$('currency').onchange=()=>{state.currency=$('currency').value;state.page=1;refresh();};
for(const id of ['reason','status'])$(id).onchange=()=>{state.page=1;loadQueue().catch(e=>notify(e.message,true));};
$('previous').onclick=()=>{state.page--;loadQueue().catch(e=>notify(e.message,true));};$('next').onclick=()=>{state.page++;loadQueue().catch(e=>notify(e.message,true));};
$('export').onclick=()=>{const p=params();p.delete('page');p.delete('limit');window.location='/api/export.csv?'+p;};
$('run').onclick=async()=>{const b=$('run');b.disabled=true;try{const r=await api('/api/reconcile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({as_of:$('asof').value})});notify(`Snapshot completed: ${number(Object.values(r.counts).reduce((a,b)=>a+b,0))} items classified in ${r.duration_ms.toFixed(0)} ms on this workstation.`);state.page=1;await refresh();}catch(e){notify(e.message,true);}finally{b.disabled=false;}};
for(const source of ['ledger','partner'])$(source+'-file').onchange=async e=>{const file=e.target.files[0];if(!file)return;try{const r=await api('/api/ingest/'+source,{method:'POST',headers:{'Content-Type':'text/csv'},body:await file.arrayBuffer()});notify(r.replayed?`Identical ${source} file already imported. Existing batch reused.`:`Imported ${number(r.rows)} ${source} rows atomically. Run reconciliation to update the snapshot.`);await refresh();}catch(error){notify(error.message,true);}finally{e.target.value='';}};
async function health(){try{const r=await fetch('/health/ready');$('health').textContent=r.ok?'Service ready':'Database unavailable';$('health').style.color=r.ok?'#397d5d':'#b66d54';}catch{$('health').textContent='Service unavailable';}}
refresh();health();setInterval(health,30000);
