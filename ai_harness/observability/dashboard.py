"""Static loopback dashboard shell; all operational data comes from authenticated APIs."""

from __future__ import annotations


DASHBOARD_HTML = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Harness · Observability</title>
  <style>
    :root { color-scheme: dark; --bg:#08111f; --panel:#101d2f; --line:#24364d; --muted:#91a2b8; --text:#eef5ff; --cyan:#59d8ff; --green:#67e8a5; --amber:#ffc56e; --red:#ff7e91; }
    * { box-sizing:border-box; }
    body { margin:0; min-height:100vh; color:var(--text); background:radial-gradient(circle at 85% -10%,#17365a 0,transparent 34%),var(--bg); font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; }
    main { width:min(1480px,calc(100% - 32px)); margin:0 auto; padding:28px 0 48px; }
    header { display:flex; justify-content:space-between; align-items:flex-end; gap:24px; margin-bottom:24px; }
    h1 { margin:4px 0 0; font:700 clamp(28px,4vw,52px)/1.05 system-ui,sans-serif; letter-spacing:-.04em; }
    h2 { margin:0 0 14px; font:650 16px/1.2 system-ui,sans-serif; }
    .eyebrow { color:var(--cyan); letter-spacing:.14em; text-transform:uppercase; font-size:11px; }
    .status { color:var(--muted); text-align:right; }
    .status strong { color:var(--green); }
    .toolbar { display:flex; gap:8px; flex-wrap:wrap; margin:0 0 22px; }
    input,button { border:1px solid var(--line); border-radius:8px; padding:10px 12px; color:var(--text); background:#0b1728; font:inherit; }
    input { flex:1; min-width:220px; }
    button { cursor:pointer; background:#15324d; }
    button:hover { border-color:var(--cyan); }
    .grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }
    .card,.panel { border:1px solid var(--line); background:linear-gradient(145deg,rgba(19,36,58,.96),rgba(12,24,41,.96)); border-radius:12px; box-shadow:0 18px 50px rgba(0,0,0,.18); }
    .card { padding:16px; min-height:118px; }
    .label { color:var(--muted); text-transform:uppercase; letter-spacing:.09em; font-size:10px; }
    .value { margin-top:10px; font:700 28px/1 system-ui,sans-serif; }
    .detail { color:var(--muted); margin-top:8px; font-size:11px; }
    .content { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:12px; }
    .panel { padding:18px; min-width:0; }
    .wide { grid-column:1/-1; }
    .scroll { overflow:auto; max-height:360px; }
    table { width:100%; border-collapse:collapse; white-space:nowrap; }
    th,td { padding:9px 8px; border-bottom:1px solid var(--line); text-align:left; font-size:12px; }
    th { color:var(--muted); font-weight:500; position:sticky; top:0; background:#101d2f; }
    .pill { display:inline-block; border:1px solid var(--line); border-radius:99px; padding:2px 7px; }
    .ok { color:var(--green); }.warn { color:var(--amber); }.bad { color:var(--red); }
    .empty { color:var(--muted); padding:18px 0; }
    @media (max-width:1000px) { .grid { grid-template-columns:repeat(2,1fr); }.content { grid-template-columns:1fr; }.wide { grid-column:auto; } }
    @media (max-width:600px) { main { width:min(100% - 20px,1480px); padding-top:18px; }.grid { grid-template-columns:1fr; } header { align-items:flex-start; flex-direction:column; }.status { text-align:left; } }
  </style>
</head>
<body>
<main>
  <header><div><div class="eyebrow">Agent intelligence platform</div><h1>Outer loop, visible.</h1></div><div class="status" id="status">Waiting for metrics</div></header>
  <div class="toolbar"><input id="token" type="password" autocomplete="off" placeholder="Control-plane bearer token (session only)"><button id="connect">Connect</button><button id="refresh">Refresh</button></div>
  <section class="grid" id="cards"></section>
  <section class="content">
    <article class="panel"><h2>Workers</h2><div class="scroll" id="workers"></div></article>
    <article class="panel"><h2>Queue</h2><div class="scroll" id="queue"></div></article>
    <article class="panel wide"><h2>Runs</h2><div class="scroll" id="runs"></div></article>
    <article class="panel wide"><h2>Recent spans</h2><div class="scroll" id="spans"></div></article>
  </section>
</main>
<script>
const $=id=>document.getElementById(id); const esc=v=>String(v??'—');
function cell(text,cls=''){const td=document.createElement('td');td.textContent=esc(text);if(cls)td.className=cls;return td}
function table(target,columns,rows){const host=$(target);host.replaceChildren();if(!rows.length){const d=document.createElement('div');d.className='empty';d.textContent='No evidence yet';host.append(d);return}const t=document.createElement('table'),h=document.createElement('thead'),hr=document.createElement('tr');columns.forEach(c=>{const th=document.createElement('th');th.textContent=c[0];hr.append(th)});h.append(hr);t.append(h);const b=document.createElement('tbody');rows.forEach(row=>{const tr=document.createElement('tr');columns.forEach(c=>tr.append(cell(c[1](row),c[2]?c[2](row):'')));b.append(tr)});t.append(b);host.append(t)}
function fmt(value,suffix=''){return value===null||value===undefined?'unknown':`${Number(value).toLocaleString(undefined,{maximumFractionDigits:2})}${suffix}`}
function render(data){const o=data.overview||{},lat=data.latency||{},cost=data.costs||{},cards=[['Queue depth',o.queue_depth,`active ${o.active_tasks||0}`],['Active workers',o.active_workers,`stalled ${o.stalled_workers||0}`],['Run latency p95',fmt(lat.run_seconds?.p95,'s'),`queue p95 ${fmt(lat.queue_wait_seconds?.p95,'s')}`],['Known cost',fmt(cost.known_usd,' USD'),`${cost.unknown_runs||0} runs unknown`],['Retries',data.retries?.total||0,`${data.retries?.tasks_retried||0} tasks`],['Loop iterations',data.loops?.total_iterations||0,`${data.loops?.runs_with_loops||0} runs`],['PR time p95',fmt(lat.pr_time_seconds?.p95,'s'),`${lat.pr_time_seconds?.samples||0} samples`],['Failures',data.failures?.total||0,`${o.human_interventions||0} human interventions`]];const host=$('cards');host.replaceChildren();cards.forEach(x=>{const a=document.createElement('article');a.className='card';for(const [cls,val] of [['label',x[0]],['value',x[1]],['detail',x[2]]]){const d=document.createElement('div');d.className=cls;d.textContent=esc(val);a.append(d)}host.append(a)});
table('workers',[['Worker',r=>r.worker_id],['Status',r=>r.status,r=>['healthy','starting'].includes(r.status)?'ok':'warn'],['Task',r=>r.current_task_id||'—'],['Restarts',r=>r.restart_count]],data.workers?.items||[]);
table('queue',[['ID',r=>r.id],['Task',r=>r.task_key],['Status',r=>r.status,r=>r.status==='completed'?'ok':r.status==='dead_letter'?'bad':'warn'],['Attempts',r=>r.attempts],['Run',r=>r.run_id||'—']],data.queue?.items||[]);
table('runs',[['Run',r=>r.run_id],['Project',r=>r.project],['Status',r=>r.status,r=>r.status==='completed'?'ok':r.failure_count?'bad':'warn'],['Latency',r=>fmt(r.elapsed_seconds,'s')],['Tokens',r=>r.tokens_used],['Cost',r=>fmt(r.cost_usd,' USD')],['Loops',r=>r.loop_iterations],['Failures',r=>r.failure_count]],data.runs?.items||[]);
table('spans',[['Span',r=>r.name],['Run',r=>r.run_id],['Status',r=>r.status,r=>r.status==='error'?'bad':'ok'],['Duration',r=>fmt(r.duration_ms,'ms')],['Trace',r=>(r.trace_id||'').slice(0,12)]],data.tracing?.items||[]);
$('status').replaceChildren();const strong=document.createElement('strong');strong.textContent='LIVE';$('status').append(strong,document.createTextNode(` · ${new Date((data.generated_at||0)*1000).toLocaleTimeString()}`));}
async function load(){const token=$('token').value.trim();if(token)sessionStorage.setItem('harness-token',token);const headers=token?{Authorization:`Bearer ${token}`}:{},res=await fetch('/metrics',{headers,cache:'no-store'});if(!res.ok)throw new Error(`metrics ${res.status}`);render(await res.json())}
async function refresh(){try{await load()}catch(err){$('status').textContent=`Disconnected · ${err.message}`}}
$('token').value=sessionStorage.getItem('harness-token')||'';$('connect').addEventListener('click',refresh);$('refresh').addEventListener('click',refresh);refresh();setInterval(refresh,15000);
</script>
</body></html>'''
