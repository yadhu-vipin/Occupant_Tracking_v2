const S={layout:null,occ:{},heat:{},hist:[],b:null,z:null};
/* Two back ends, one page. "integrated": served by the real server under /lane-d, backed by the pipeline's data and RBAC,
   every call carries the bearer token of the selected role. "standalone": the offline demo server, open endpoints. */
const MODE=document.querySelector('meta[name="dsts-mode"]')?.content||"standalone",INTEG=MODE==="integrated",BASE=INTEG?"/lane-d":"";
const ROLE_PERSONA={officer:"dean",analyst:"teacher",self:"student"},TOK={},PRIN={},PH={};
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
/* zone names of the real zone graph (dsts/state/zones.py) */
const ZONES=[["z1","Entrance"],["z2","Mail room"],["z3","Office"],["z4","Lounge"],["z5","Conference"],["z6","Classroom"],["z7","Cafeteria"],["z8","Exit"],["zT","Transition"]];
const QUERIES=[["Q1","Did any occupant stay in building B after time T?",["building","time"]],["Q2","Were visitors in building B more numerous than registered occupants at time T?",["building","time"]],["Q3","Did occupant O leave building B before time T?",["occupant","building","time"]],["Q4","Did occupant O enter the building between T1 and T2?",["occupant","building","start","end"]],["Q5","Did occupant O visit all zones in building B?",["occupant","building"]],["Q6","Where was occupant O at time T?",["occupant","time"]],["Q7","How many zones did occupant O visit during T1–T2?",["occupant","start","end"]],["Q8","At what time did occupant O visit zone Z for the first time?",["occupant","zone"]],["Q9","How long did occupant O spend at zone Z during the first visit?",["occupant","zone"]],["Q10","Who was in zone Z at time T?",["zone","time"]],["Q11","Where was occupant O during T1–T2?",["occupant","start","end"]],["Q12","Who was in zone Z from T1 to T2?",["zone","start","end"]],["Q13","Who all visited zone Z?",["zone"]],["TRACK","Full trajectory of occupant O",["occupant"]]];
const COL=["#7c5cff","#4f7cff","#22c55e","#f59e0b","#ec4899"];
const safe=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const hdr=role=>INTEG&&TOK[role||"officer"]?{Authorization:"Bearer "+TOK[role||"officer"]}:{};
const fail=async r=>{let m=`${r.status} ${r.statusText}`;try{m=(await r.json()).detail||m}catch(e){}throw new Error(m)};
const api=async(u,o={},role)=>{const r=await fetch(BASE+u,{...o,headers:{...(o.headers||{}),...hdr(role)}});if(!r.ok)await fail(r);return r.json()};
const post=(u,b,role)=>api(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b||{})},role);
async function login(){for(const[role,persona]of Object.entries(ROLE_PERSONA)){const r=await fetch("/api/auth/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({persona})});if(!r.ok)await fail(r);const d=await r.json();TOK[role]=d.token;PRIN[role]=d.principal}}
const B=()=>S.layout?.buildings||[];
const fmtT=t=>{if(!t)return"—";if(/^\d{2}:\d{2}(:\d{2})?$/.test(t))return t;const d=new Date(String(t).replace(" ","T"));return isNaN(d)?String(t):d.toLocaleTimeString([],{hour12:false})};

/* navigation */
function go(v){$$(".nav[data-view]").forEach(n=>n.classList.toggle("active",n.dataset.view===v));$$(".view").forEach(p=>p.classList.toggle("active",p.id==="v-"+v));if(v==="security")refreshSecurity();}
$$(".nav[data-view]").forEach(n=>n.onclick=()=>go(n.dataset.view));$$("[data-go]").forEach(a=>a.onclick=()=>go(a.dataset.go));
$("#theta").oninput=e=>$("#theta-value").textContent=(e.target.value/100).toFixed(2);
setInterval(()=>{const d=new Date();$("#clk-d").textContent=d.toLocaleDateString([],{weekday:"short",day:"2-digit",month:"short",year:"numeric"});$("#clk-t").textContent=d.toLocaleTimeString([],{hour12:false})},1000);

/* data */
const rowsFrom=s=>{const o=[];Object.entries(s||{}).forEach(([z,r])=>(r||[]).forEach(x=>o.push({...x,zone:x.zone||z})));return o};
function renderRows(t,rows){const b=$(t);b.innerHTML=rows.length?rows.map(r=>`<tr><td>${safe(r.building_id)}</td><td>${safe(r.zone)}</td><td>${safe(r.occupant)}</td><td>${r.probability==null?"—":Number(r.probability).toFixed(2)}</td><td>${safe(r.time_text||r.time||r.details||"—")}</td></tr>`).join(""):'<tr><td colspan="5" class="empty">No matching records in the available data.</td></tr>'}
async function refresh(){
 const [o,h,q]=await Promise.all([api("/api/occupancy"),api("/api/heatmap"),api("/api/events").catch(()=>[])]);
 S.occ=o;S.heat=h;S.hist=Array.isArray(q)?q:(q.rows||[]);const rows=rowsFrom(o);renderRows("#occupancy-body",rows);$("#occupancy-count").textContent=`${rows.length} records`;
 renderKpis();renderCampus();renderActivity();renderZones();
}
const avg=a=>a.length?a.reduce((x,y)=>x+y,0)/a.length:0;
function renderKpis(){
 const bs=B(),conf=S.hist.map(r=>r.probability).filter(p=>p!=null),h=Math.floor(conf.length/2);
 const d=conf.length>3?(avg(conf.slice(h))-avg(conf.slice(0,h)))*100:null;
 const K=[["▦","Total Buildings",bs.length,"Active","","#7c5cff"],["▣","Total Zones",bs.length*ZONES.length,"Configured","bl","#4f7cff"],["◉","Tracked Occupants",bs.reduce((n,b)=>n+b.occupants.length,0),"Registered","","#22c55e"],["⚡","Live Events",S.hist.length,"Generated","bl","#ec4899"],["◎","Avg Confidence",conf.length?avg(conf).toFixed(2):"—",d==null?"No trend yet":(d>=0?"+":"")+d.toFixed(0)+"%",d!=null&&d<0?"dn":"","#f59e0b"]];
 $("#kpis").innerHTML=K.map(k=>`<div class="kpi"><div class="ic" style="background:${k[5]}26;color:${k[5]};box-shadow:0 0 16px ${k[5]}33">${k[0]}</div><div><small class="muted">${k[1]}</small><b>${k[2]}</b><span class="tag ${k[4]}">${k[3]}</span></div></div>`).join("");
}
function renderActivity(){
 const r=S.hist.slice(-6).reverse(),bi=id=>Math.max(0,B().findIndex(b=>b.id===id));
 $("#activity").innerHTML=r.length?r.map(e=>`<div class="act"><div class="b" style="background:${COL[bi(e.building_id)%5]}33;color:${COL[bi(e.building_id)%5]}">${safe((e.occupant||"?").slice(-4))}</div><div><b>${safe(e.occupant||"unknown")}</b><br><span>moved to ${safe(e.building_id)}-${safe(e.zone)}</span></div><span>${fmtT(e.time)}</span></div>`).join(""):'<p class="muted">No events yet. Use Event Generator or the B1 → B5 Demo.</p>';
}
/* isometric campus: any number of buildings on a grid (the real project has 10) */
function box(cx,cy,h,c){const a=30,b=17,P=(...p)=>p.map(x=>x.join(",")).join(" ");
 const win=(x1,y1,x2,y2)=>[1,2,3].map(i=>{const k=i*h/4;return `<line x1="${x1}" y1="${y1-k}" x2="${x2}" y2="${y2-k}" stroke="#bcd0ff" stroke-opacity=".55" stroke-width="2" stroke-dasharray="3 5"/>`}).join("");
 return `<g filter="url(#gl)"><polygon points="${P([cx-a,cy-h],[cx,cy-h+b],[cx,cy+b],[cx-a,cy])}" fill="${c}" fill-opacity=".38"/><polygon points="${P([cx,cy-h+b],[cx+a,cy-h],[cx+a,cy],[cx,cy+b])}" fill="${c}" fill-opacity=".6"/><polygon points="${P([cx,cy-h-b],[cx+a,cy-h],[cx,cy-h+b],[cx-a,cy-h])}" fill="${c}" stroke="#fff" stroke-opacity=".5"/>${win(cx-a+5,cy-5+b/2-3,cx-5,cy+b-8)}${win(cx+5,cy+b-8,cx+a-5,cy-5)}</g>`}
function campusPos(n){const cols=Math.min(5,Math.max(1,n)),rows=Math.ceil(n/cols),out=[];
 for(let i=0;i<n;i++){const u=i%cols-(cols-1)/2,w=Math.floor(i/cols)-(rows-1)/2;out.push([340+(u-w)*88,172+(u+w)*44])}return out}
function renderCampus(){
 const bs=B(),pos=campusPos(bs.length);
 const G=bs.map((q,i)=>({q,x:pos[i][0],y:pos[i][1],h:38+(q.occupants.length%4)*8,c:COL[i%COL.length]}));
 let s=`<svg viewBox="0 -62 680 410"><defs><filter id="gl"><feGaussianBlur stdDeviation="2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs><polygon points="340,20 660,170 340,320 20,170" fill="#0c1334" stroke="#3b46a8" stroke-opacity=".6"/>`;
 for(let i=1;i<8;i++){const t=i/8;s+=`<line x1="${340-320*t}" y1="${20+150*t}" x2="${340+320*(1-t)}" y2="${170+150*(1-t)}" stroke="#4452c9" stroke-opacity=".18"/><line x1="${340+320*t}" y1="${20+150*t}" x2="${340-320*(1-t)}" y2="${170+150*(1-t)}" stroke="#4452c9" stroke-opacity=".18"/>`}
 G.slice().sort((p,q)=>p.y-q.y||p.x-q.x).forEach(g=>s+=box(g.x,g.y,g.h,g.c));
 G.forEach(g=>{const n=Object.values(S.heat[g.q.id]||{}).reduce((p,q)=>p+q,0),lab=String(g.q.id).replace(/^building_/,"B");
  s+=`<g transform="translate(${g.x-30},${g.y-g.h-44})"><rect width="60" height="28" rx="8" fill="#0b1030" stroke="${g.c}" stroke-opacity=".8"/><text x="30" y="12" text-anchor="middle" fill="#fff" font-weight="700" font-size="11">${safe(lab)}</text><text x="30" y="23" text-anchor="middle" fill="#aab4e8" font-size="8.5">${g.q.occupants.length} occ · ${n} here</text></g>`});
 $("#campus").innerHTML=s+"</svg>";$("#campus-sub").textContent=`${bs.length} buildings`;
}
/* zones */
const heat=(v,m)=>{if(!v)return"#2b3555";const t=Math.min(v/Math.max(m,1),1);return t<.34?"#22c55e":t<.67?"#facc15":"#f97316"};
function counts(b){if(b!=="all")return S.heat[b]||{};const o={};Object.values(S.heat).forEach(c=>Object.entries(c||{}).forEach(([z,n])=>o[z]=(o[z]||0)+n));return o}
function renderZones(){
 if(!S.b&&B().length)S.b=B()[0].id;
 const sel=$("#heatmap-building");if(!sel.options.length)sel.innerHTML='<option value="all">All Buildings</option>'+B().map(b=>`<option>${b.id}</option>`).join("");sel.value=S.b;
 $("#bcards").innerHTML=B().map(b=>`<button class="bc ${S.b===b.id?"on":""}" data-b="${b.id}"><b>${b.id}</b><span>${b.occupants.length} occupants</span></button>`).join("");
 $$(".bc").forEach(x=>x.onclick=()=>{S.b=x.dataset.b;S.z=null;S.win?applyWindow():renderZones()});
 const win=S.win&&S.win.building===S.b,c=win?winCounts():counts(S.b),m=Math.max(1,...Object.values(c));
 $("#zmap-title").textContent=S.b==="all"?"All buildings · zones":`${S.b} Zones`;
 $("#plane").innerHTML=ZONES.map(([z,l])=>{const n=c[z]||0,h=8+Math.round(n/m*26),col=heat(n,m);return `<button class="tile ${S.z===z?"sel":""}" data-z="${z}" style="--c:${col};--h:${h}px" title="${l}: ${n} ${win?"in the time frame":"tracked"}"><span class="f1"></span><span class="f2"></span><span class="t"><em>${z}<br><small>${n}</small></em></span></button>`}).join("");
 $$(".tile").forEach(t=>t.onclick=()=>{S.z=t.dataset.z;renderZones()});
 zoneDetail(c,m);renderZoneTable();
}
function zoneDetail(c,m){if(S.win&&S.win.building===S.b)return windowDetail(S.z||"z1");
 const z=S.z||"z1",lab=(ZONES.find(x=>x[0]===z)||[])[1],n=c[z]||0,t=n/m,lvl=!n?["lo","Empty"]:t>.66?["hi","High Occupancy"]:t>.33?["md","Medium Occupancy"]:["lo","Low Occupancy"];
 const rows=rowsFrom(S.occ).filter(r=>(S.b==="all"||r.building_id===S.b)&&r.zone===z),ev=S.hist.filter(r=>(S.b==="all"||r.building_id===S.b)&&r.zone===z).map(r=>r.timestamp);
 const bk=Array(10).fill(0);if(ev.length){const lo=Math.min(...ev),sp=Math.max(...ev)-lo||1;ev.forEach(x=>bk[Math.min(9,Math.floor((x-lo)/sp*10))]++)}const mx=Math.max(1,...bk);
 const cf=rows.map(r=>r.probability).filter(p=>p!=null);
 $("#zone-detail").innerHTML=`<div class="ch"><h3>Zone Details</h3></div><div class="ch"><div><h2>${safe(S.b==="all"?"All":S.b)} · ${z}</h2><span class="muted">${safe(lab)}</span></div><span class="badge ${lvl[0]}">${lvl[1]}</span></div><div class="stat2"><div><small>Tracked Occupants</small><b>${n}</b></div><div><small>Avg Confidence</small><b>${cf.length?avg(cf).toFixed(2):"—"}</b></div></div><h3>Recent Activity</h3><div class="bars">${bk.map(v=>`<i style="height:${v/mx*100}%" title="${v} events"></i>`).join("")}</div><div class="chips">${rows.length?rows.map(r=>`<span class="chip">${safe(r.occupant)}</span>`).join(""):'<span class="muted">No current occupant records for this zone.</span>'}</div>`;
}
$("#heatmap-building").onchange=e=>{S.b=e.target.value;S.z=null;if(S.win&&S.b==="all")clearWindow();else S.win?applyWindow():renderZones()};
/* time-frame filter: who was in each zone of the selected building between two moments */
const winCounts=()=>Object.fromEntries(ZONES.map(([z])=>[z,S.win?.zones?.[z]?.count||0]));
const fmtW=t=>t?String(t).replace("T"," "):"—";
async function applyWindow(){
 if(S.b==="all"||!S.b)S.b=B()[0]?.id;
 const role=$("#zf-role").value,q=new URLSearchParams({building:S.b});
 if($("#zf-start").value)q.set(INTEG?"start":"start",$("#zf-start").value);if($("#zf-end").value)q.set("end",$("#zf-end").value);
 if(INTEG)q.set("purpose",$("#zf-purpose").value);else q.set("role",role);
 try{S.win=await api("/api/zone-presence?"+q.toString(),{},role);
  $("#zf-note").textContent=`${S.win.building}: ${S.win.total} different people between ${fmtW(S.win.start)} and ${fmtW(S.win.end)}`+(S.win.withheld?` — ${S.win.withheld} not named for this role.`:".")}
 catch(e){S.win=null;$("#zf-note").textContent="Time frame failed: "+e.message}
 renderZones();
}
function clearWindow(){S.win=null;$("#zf-note").textContent="";renderZones()}
function windowDetail(z){
 const w=S.win.zones[z]||{count:0,disclosed:0,occupants:[]},lab=(ZONES.find(x=>x[0]===z)||[])[1];
 $("#zone-detail").innerHTML=`<div class="ch"><h3>Zone Details</h3></div><div class="ch"><div><h2>${safe(S.b)} · ${z}</h2><span class="muted">${safe(lab)}</span></div><span class="badge ${w.count?"md":"lo"}">${w.count} in time frame</span></div><div class="stat2"><div><small>People in window</small><b>${w.count}</b></div><div><small>Named for you</small><b>${w.disclosed}</b></div></div><h3>${fmtW(S.win.start)} → ${fmtW(S.win.end)}</h3><div class="chips">${w.occupants.length?w.occupants.map(o=>`<span class="chip" title="first ${safe(o.first_seen)} · last ${safe(o.last_seen)}${o.visitor?" · visitor from "+safe(o.home_building):""}">${safe(o.occupant)}${o.visitor?" ↗":""}</span>`).join(""):`<span class="muted">${w.count?"Present, but your role may not see who.":"Nobody was in this zone in the time frame."}</span>`}</div>`;
}
function renderZoneTable(){
 const box=$("#zone-table");if(!(S.win&&S.win.building===S.b)){box.style.display="none";return}
 box.style.display="";
 const rows=ZONES.map(([z,l])=>{const w=S.win.zones[z]||{count:0,disclosed:0,occupants:[]};
  const who=w.occupants.map(o=>`<span class="chip" title="first ${safe(o.first_seen)} · last ${safe(o.last_seen)}">${safe(o.occupant)}${o.visitor?" ↗":""}</span>`).join("")||(w.count?'<span class="muted">not named for this role</span>':'<span class="muted">—</span>');
  return `<tr><td>${z} · ${safe(l)}</td><td>${w.count}</td><td><div class="chips">${who}</div></td></tr>`}).join("");
 box.innerHTML=`<div class="ch"><h3>${safe(S.b)} — who was where, ${fmtW(S.win.start)} → ${fmtW(S.win.end)}</h3><span class="pill">${S.win.total} people</span></div><div class="table-wrap"><table><thead><tr><th>Zone</th><th>People</th><th>Who (↗ = visitor; hover for first / last seen)</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}
$("#zf-apply").onclick=applyWindow;$("#zf-clear").onclick=clearWindow;
if(!INTEG&&$("#zf-purpose-field"))$("#zf-purpose-field").style.display="none";
/* security */
let secTab="logs",SEC=null;
const ROLES=INTEG?[["officer","Acts as the Dean of the demo building: every movement of that building's teachers and students (L4, any purpose), and of other buildings' Deans; nobody else"],["analyst","Acts as a Teacher of the demo building: only the students assigned to them (class-teacher), in any building; nobody else"],["self","Acts as a Student of the demo building: their own data (L4), and where the teachers assigned to them are now (L3); nobody else"]]:[["officer","Full detail: time, zone, occupant, probability"],["analyst","Aggregated: pseudonymised occupant handle, no raw probability"],["self","Only rows that belong to the requesting occupant"]];
async function refreshSecurity(){
 try{const [s,ev]=await Promise.all([api("/api/security-summary"),api("/api/security-log")]);SEC={s,ev};}catch(e){console.warn(e);return}
 const K=[["Authenticated Users",SEC.s.authenticated_users,"#22c55e","◉"],["Active Sessions",SEC.s.active_sessions,"#4f7cff","◔"],["API Requests",SEC.s.api_requests,"#7c5cff","⇄"],["Blocked Requests",SEC.s.blocked_requests,"#ef4444","⊘"]];
 $("#sec-kpis").innerHTML=K.map(k=>`<div class="kpi"><div class="ic" style="background:${k[2]}26;color:${k[2]}">${k[3]}</div><div><small class="muted">${k[0]}</small><b>${k[1]}</b></div></div>`).join("");renderSecTab();
}
function renderSecTab(){
 const el=$("#sec-body");if(!SEC)return;
 if(secTab==="logs"){const rows=[...SEC.ev.map(e=>INTEG?({t:e.time,u:e.building_id,a:e.kind,r:e.detail,ok:e.kind==="auth_permit"}):({t:e.time,u:"system",a:e.kind,r:e.building_id,ok:false})),...SEC.s.access_log.map(e=>({t:e.time,u:e.user,a:e.action,r:e.resource,ok:e.status==="Success"}))].sort((a,b)=>b.t-a.t).slice(0,50);
  el.innerHTML=`<table><thead><tr><th>Time</th><th>User / Role</th><th>Action</th><th>Resource</th><th>Status</th></tr></thead><tbody>${rows.length?rows.map(r=>`<tr><td>${new Date(r.t*1000).toLocaleTimeString([],{hour12:false})}</td><td>${safe(r.u)}</td><td>${safe(r.a)}</td><td>${safe(r.r)}</td><td><span class="st ${r.ok?"":"bad"}">${r.ok?"Success":"Blocked"}</span></td></tr>`).join(""):'<tr><td colspan="5" class="empty">No access activity recorded yet.</td></tr>'}</tbody></table>`}
 else if(secTab==="roles")el.innerHTML=`<table><thead><tr><th>Role</th><th>Access</th></tr></thead><tbody>${ROLES.map(r=>`<tr><td>${r[0]}</td><td>${r[1]}</td></tr>`).join("")}</tbody></table>`;
 else if(secTab==="policies")el.innerHTML=`<table><thead><tr><th>Policy</th><th>Description</th></tr></thead><tbody><tr><td>Role-based filtering</td><td>${INTEG?"Two layers before any answer: infrastructure RBAC (Layer 1) and the building-scoped persona policy (Layer 2); every decision is audited.":"Every query response passes through the access policy before it is returned."}</td></tr><tr><td>Replay protection</td><td>${INTEG?"Sealed inter-building payloads carry a nonce and timestamp; the replay guard runs after the signature check.":"Inter-building messages carry nonces; replays are detected and logged."}</td></tr><tr><td>Unknown buildings</td><td>${INTEG?"Peers without a CA-issued, unexpired, unrevoked certificate are refused (fail closed).":"Messages from unregistered buildings are rejected and logged."}</td></tr></tbody></table>`;
 else el.innerHTML=`<table><thead><tr><th>Mechanism</th><th>Description</th></tr></thead><tbody>${INTEG?`<tr><td>Campus CA + mTLS</td><td>Node certificates bind the Ed25519 signing key and the X25519 key-agreement key; a session is required per building pair.</td></tr><tr><td>Sealed payloads</td><td>X25519 → HKDF-SHA256 → AES-128-GCM, Ed25519 signature, reply bound to its request.</td></tr><tr><td>API tokens</td><td>HMAC-signed bearer tokens (1 h); single-use tickets for the live socket.</td></tr>`:`<tr><td>HMAC signing</td><td>Inter-building transport is signed with the shared secret from <code>config/campus.yaml</code> (offline demo).</td></tr>`}</tbody></table>`;
}
$$("#subtabs button").forEach(b=>b.onclick=()=>{secTab=b.dataset.st;$$("#subtabs button").forEach(x=>x.classList.toggle("active",x===b));renderSecTab()});
/* queries */
function initQueries(){const s=$("#query-kind");QUERIES.forEach(q=>s.add(new Option(`${q[0]} — ${q[1]}`,q[0])));s.onchange=renderQF;renderQF()}
/* time boxes start at office hours on the latest simulated day, so a Teacher or Student query is not refused for being at 23:59 */
const dayD=()=>S.layout?.default_date||new Date().toISOString().slice(0,10),defTime=k=>`${dayD()}T${{time:"12:00",start:"08:00",end:"18:00"}[k]||"12:00"}`;
function fieldMarkup(k){const d={building:["Building ID","text",PH.building||"B1"],occupant:["Occupant ID","text",PH.occupant||"O217"],zone:["Zone ID","select"],time:["Time T","datetime-local"],start:["Start time T1","datetime-local"],end:["End time T2","datetime-local"]}[k];
 return d[1]==="select"?`<label class="field">${d[0]}<select name="${k}">${ZONES.map(z=>`<option value="${z[0]}">${z[0]} — ${z[1]}</option>`).join("")}</select></label>`:`<label class="field">${d[0]}<input name="${k}" type="${d[1]}" value="${d[1]==="datetime-local"?defTime(k):""}" placeholder="${d[2]||""}"></label>`}
function renderQF(){const q=QUERIES.find(q=>q[0]===$("#query-kind").value)||QUERIES[0];$("#query-description").textContent=q[1];$("#query-fields").innerHTML=q[2].map(fieldMarkup).join("")}
function qPayload(){const p={};$("#query-fields").querySelectorAll("[name]").forEach(e=>{if(e.value)p[e.name]=e.value});return{query_id:$("#query-kind").value,role:$("#q-role").value,purpose:$("#q-purpose")?$("#q-purpose").value:"general_lookup",...p}}
async function runQuery(p=qPayload()){try{const d=await post("/api/query",p,p.role);const dec=d.decision?` Policy ${d.decision.policy_rule||"—"} · ${d.decision.disclosure_level||""}.`:"";$("#query-note").textContent=(d.redacted?`Role policy applied for ${p.role}; some details may be hidden.`:`Executed ${p.query_id||"query"} using available tracking data.`)+dec;$("#query-answer").textContent=d.answer||`${(d.rows||[]).length} matching record(s).`;renderRows("#query-body",d.rows||[]);if($("#q-speak").checked)speak(d.answer||"Query completed.");}catch(e){$("#query-note").textContent="Query failed: "+e.message;$("#query-answer").textContent="The API could not execute this query: "+e.message;renderRows("#query-body",[])}}
$("#btn-query").onclick=()=>runQuery();
const showAs=()=>{const el=$("#q-as");if(!el)return;const r=$("#q-role").value;el.textContent=INTEG&&PRIN[r]?`Acting as ${r} → occupant ${PRIN[r]} (${ROLE_PERSONA[r]}). The answer and what is hidden follow that person's role.`:""};
$("#q-role").onchange=showAs;
if(!INTEG&&$("#purpose-field"))$("#purpose-field").style.display="none";
$("#btn-clear-query").onclick=()=>{$("#query-fields").querySelectorAll("input").forEach(x=>x.value="");$("#query-note").textContent="";$("#query-answer").textContent="";renderRows("#query-body",[])};
function speak(t){if(!("speechSynthesis"in window))return;speechSynthesis.cancel();speechSynthesis.speak(new SpeechSynthesisUtterance(t))}
function parseTranscript(t){const s=t.toLowerCase(),o={};const q=s.match(/\bq\s*(\d{1,2})\b/);if(q)o.query_id="Q"+q[1];const oc=s.match(/\bo\s*-?\s*(\d{2,4})\b/);if(oc)o.occupant="O"+oc[1];const b=s.match(/\bb\s*-?\s*(1|2|3|4|5)\b/);if(b)o.building="B"+b[1];const z=s.match(/\bz\s*-?\s*(t|\d)\b/);if(z)o.zone=z[1]==="t"?"zT":"z"+z[1];for(const[c,l]of ZONES)if(s.includes(l.toLowerCase()))o.zone=c;if(!o.query_id){if(s.includes("who")&&s.includes("zone"))o.query_id=s.includes("between")?"Q12":"Q10";else if(s.includes("how long"))o.query_id="Q9";else if(s.includes("how many zones"))o.query_id="Q7";else if(s.includes("first time"))o.query_id="Q8";else if(s.includes("where")&&s.includes("during"))o.query_id="Q11";else if(s.includes("where"))o.query_id="Q6";else if(s.includes("all zones"))o.query_id="Q5";else if(s.includes("enter"))o.query_id="Q4";else if(s.includes("leave"))o.query_id="Q3";else if(s.includes("visitor"))o.query_id="Q2";else if(s.includes("stay")||s.includes("after"))o.query_id="Q1";else if(s.includes("visited"))o.query_id="Q13"}return o}
function setupVoice(){const btn=$("#btn-voice"),st=$("#voice-status"),Sp=window.SpeechRecognition||window.webkitSpeechRecognition;if(!Sp){btn.disabled=true;st.textContent="Speech recognition is not supported in this browser. Use Chrome or type the query.";return}
 const rec=new Sp();rec.lang="en-US";rec.interimResults=false;btn.onclick=()=>{try{rec.start();st.textContent="Listening… say a question such as “Q10, who was in zone z1 at time T?”"}catch(e){st.textContent="Microphone could not start: "+e.message}};
 rec.onresult=e=>{const t=e.results[0][0].transcript,p=parseTranscript(t);st.textContent=`Heard: “${t}”`;if(p.query_id){$("#query-kind").value=p.query_id;renderQF()}Object.entries({occupant:p.occupant,building:p.building,zone:p.zone}).forEach(([k,v])=>{const el=$(`#query-fields [name="${k}"]`);if(el&&v)el.value=v});if(p.query_id){go("query");$("#query-note").textContent="Review the recognized parameters, add any missing times, then run the query."}else st.textContent+=" — could not identify a template. Say Q1–Q13 or use the selector."};rec.onerror=e=>st.textContent="Voice recognition error: "+e.error}
/* actions */
$("#btn-simulate").onclick=async()=>{try{await post("/api/simulate",{n_events:Number($("#sim-count").value),seed:Number($("#sim-seed").value)});await refresh()}catch(e){alert("Simulation failed: "+e.message)}};
$("#btn-walk").onclick=async()=>{try{const d=await post("/api/demo-walk");go("overview");await refresh();$("#campus-sub").textContent=`Demo walk ${d.home_building} → ${d.away_building}: ${d.events_run} events in ${Number(d.seconds).toFixed(3)}s`}catch(e){alert("Demo failed: "+e.message)}};
$("#btn-reset").onclick=async()=>{await post("/api/reset");S.z=null;await refresh();refreshSecurity()};
async function connectLive(){let q="";if(INTEG){try{const r=await fetch("/api/ws-ticket",{method:"POST",headers:hdr()});if(r.ok)q="?ticket="+encodeURIComponent((await r.json()).ticket)}catch(e){}}const ws=new WebSocket(`${location.protocol==="https:"?"wss":"ws"}://${location.host}/ws/live${q}`);ws.onopen=()=>$("#live-dot").style.background="var(--accent)";ws.onclose=()=>{$("#live-dot").style.background="var(--danger)";setTimeout(connectLive,3000)};ws.onmessage=e=>{try{const d=JSON.parse(e.data);if(d.type==="occupancy"||d.type==="simulation_tick"||d.type==="demo_walk")refresh();else if(d.heatmap&&JSON.stringify(d.heatmap)!==JSON.stringify(S.heat)){S.heat=d.heatmap;renderCampus();renderZones()}if(d.type==="security")refreshSecurity()}catch(x){console.warn(x)}}}
(async function boot(){
 try{
  if(INTEG)await login();
  S.layout=await api("/api/layout");
  $("#zf-start").value=`${dayD()}T08:00`;$("#zf-end").value=`${dayD()}T18:00`;
  const b0=B()[0];PH.building=S.layout.home_building||b0?.id;PH.occupant=S.layout.default_target||b0?.occupants?.[0];initQueries();showAs();
  await refresh();
 }catch(e){console.error(e);initQueries();$("#campus").innerHTML='<p class="muted">Could not load project API: '+safe(e.message)+(INTEG?' — start the server with DSTS_DEMO_MODE=1 and build a run first (python manage.py run --seeds 42 --days 1).':'')+'</p>'}
 setupVoice();if(!INTEG||TOK.officer)connectLive();
})();
