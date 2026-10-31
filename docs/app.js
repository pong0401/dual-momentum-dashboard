const $ = id => document.getElementById(id);
const fmt = (n, digits=2) => Number.isFinite(n) ? (n * 100).toLocaleString('en-US',{minimumFractionDigits:digits,maximumFractionDigits:digits})+'%' : '—';
const number = n => n.toLocaleString('en-US',{maximumFractionDigits:2});
const money = n => '$'+n.toLocaleString('en-US',{maximumFractionDigits:n<1000?2:0});
const palette=['#73e6c2','#789eff','#c3a4f6','#e9bb78','#70c2dd','#d88ca9','#a1aebc','#a1cb91'];
let dataset, variant='with_btc', range='ALL', archive='recent';

function el(tag, text, cls) {const x=document.createElement(tag); if(text!==undefined)x.textContent=text; if(cls)x.className=cls; return x;}
function notice(message) {$('error').textContent=message; $('error').hidden=false;}
function current(){return dataset.variants[variant];}

function drawChart(){
 const data=current().curves;
 let rows=data;
 if(range!=='ALL'){
  const end=new Date(data.at(-1).date+'T00:00:00Z'); end.setUTCFullYear(end.getUTCFullYear()-parseInt(range));
  const i=data.findIndex(d=>new Date(d.date+'T00:00:00Z')>end);
  rows=data.slice(Math.max(0,i-1));
 }
 const host=$('chart');host.replaceChildren();
 if(rows.length<2){host.append(el('p','No data for this period.'));return;}
 const width=Math.max(host.clientWidth,280), height=host.clientHeight;
 const margin={l:8,r:70,t:12,b:28},w=width-margin.l-margin.r,h=height-margin.t-margin.b;
 const series=[{key:'dual_net',color:'#73e6c2',name:'Dual net'},{key:'spy',color:'#789eff',name:'SPY'}];
 if($('show-gross').checked)series.push({key:'dual_gross',color:'#a6aeb8',name:'Dual gross'});
 if($('show-stress').checked)series.push({key:'dual_2x_cost',color:'#e9bb78',name:'2× costs'});
 const points=rows.map(r=>({...r,...Object.fromEntries(series.map(s=>[s.key,r[s.key]/rows[0][s.key]*100]))}));
 const log=$('log-scale').checked, transform=x=>log?Math.log10(x):x;
 const values=points.flatMap(r=>series.map(s=>transform(r[s.key])));
 let low=Math.min(...values),high=Math.max(...values); const pad=(high-low)*.08 || 1;low-=pad;high+=pad;
 const x=i=>margin.l+i/(points.length-1)*w,y=v=>margin.t+h-(transform(v)-low)/(high-low)*h;
 const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');
 svg.setAttribute('viewBox',`0 0 ${width} ${height}`);svg.setAttribute('role','img');svg.setAttribute('aria-label',`Growth of $100, ${rows[0].date} to ${rows.at(-1).date}, Dual and SPY`);
 const node=(tag,attrs,text)=>{const n=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);if(text!==undefined)n.textContent=text;svg.append(n);return n;};
 for(let i=0;i<5;i++){
  const v=low+(high-low)*i/4,yy=margin.t+h-i/4*h;
  node('line',{x1:margin.l,y1:yy,x2:width-margin.r,y2:yy,stroke:'#263440','stroke-width':.7,'stroke-dasharray':'3 5'});
  node('text',{x:width-margin.r+11,y:yy+4},money(log?10**v:v));
 }
 const ticks=width<500?3:6;
 for(let i=0;i<ticks;i++){
  const index=Math.round((points.length-1)*i/(ticks-1));
  node('text',{x:x(index),y:height-4,'text-anchor':i===0?'start':i===ticks-1?'end':'middle'},points[index].date.slice(0,7));
 }
 const step=Math.max(1,Math.floor(points.length/1800));
 for(const s of series){
  const sampled=points.map((r,i)=>({r,i})).filter(({i})=>i%step===0||i===points.length-1);
  const d=sampled.map(({r,i},j)=>`${j?'L':'M'}${x(i).toFixed(2)},${y(r[s.key]).toFixed(2)}`).join(' ');
  node('path',{d,fill:'none',stroke:s.color,'stroke-width':s.key==='dual_net'?2.2:1.5,'stroke-linejoin':'round','stroke-linecap':'round',opacity:s.key==='dual_gross'?.65:1});
 }
 const cross=node('line',{x1:0,y1:margin.t,x2:0,y2:height-margin.b,stroke:'#718699','stroke-dasharray':'3 3',visibility:'hidden'});
 const tip=el('div',undefined,'tooltip');tip.hidden=true;host.append(svg,tip);
 host.onpointermove=e=>{
  const box=host.getBoundingClientRect(),px=e.clientX-box.left;
  const i=Math.max(0,Math.min(points.length-1,Math.round((px-margin.l)/w*(points.length-1))));
  const r=points[i];cross.setAttribute('x1',x(i));cross.setAttribute('x2',x(i));cross.setAttribute('visibility','visible');
  tip.replaceChildren(el('strong',r.date));
  series.forEach(s=>{const line=el('div',`${s.name}  ${money(r[s.key])}`);line.style.color=s.color;tip.append(line);});
  tip.hidden=false;tip.style.left=Math.max(0,Math.min(px+15,width-190))+'px';tip.style.top='18px';
 };
 host.onpointerleave=()=>{tip.hidden=true;cross.setAttribute('visibility','hidden');};
 $('chart-period').textContent=rows[0].date+' → '+rows.at(-1).date;
}

function renderMetrics(){
 const m=current().metrics[range];
 $('cagr').textContent=fmt(m.dual_net.cagr);$('cagr-spy').textContent=`SPY ${fmt(m.spy.cagr)} · ${range==='ALL'?'Full history':range}`;
 $('total-return').textContent=fmt(m.dual_net.total_return,1);$('total-spy').textContent=`SPY ${fmt(m.spy.total_return,1)}`;
 $('drawdown').textContent=fmt(m.dual_net.max_drawdown);$('dd-spy').textContent=`SPY ${fmt(m.spy.max_drawdown)}`;
 $('sharpe').textContent=m.dual_net.sharpe.toFixed(2);$('sharpe-spy').textContent=`SPY ${m.spy.sharpe.toFixed(2)} · RF = 0`;
}

function renderAllocation(){
 const v=current().latest;
 const weights=Object.entries({...v.effective_weights_next_session,CASH:v.cash_weight_next_session}).sort((a,b)=>b[1]-a[1]);
 $('current-month').textContent=dataset.manifest.month_end_decision_date.slice(0,7) < dataset.data_asof.slice(0,7) ? dataset.data_asof.slice(0,7) : nextMonth(dataset.manifest.month_end_decision_date);
 $('allocation').replaceChildren();$('allocation-bar').replaceChildren();
 weights.forEach(([ticker,value],i)=>{
  const color=ticker==='CASH'?'#657689':palette[i%palette.length];
  if(value>0){const bar=el('span');bar.style.background=color;bar.style.width=value*100+'%';bar.title=ticker+' '+fmt(value);$('allocation-bar').append(bar);}
  const row=el('div',undefined,'allocation-row'),dot=el('i');dot.style.background=color;
  const name=el('span',ticker);if(ticker==='GC=F')name.append(el('small','Gold proxy'));if(ticker==='CASH')name.append(el('small','Uninvested'));
  row.append(dot,name,el('strong',fmt(value)));$('allocation').append(row);
 });
 $('overlay-note').textContent=`Decision ${dataset.manifest.month_end_decision_date} · overlay ${dataset.data_asof}. น้ำหนักรวมเงินสด 100% ก่อนปัดเศษ; ปรับเปลี่ยนได้ตาม daily overlay`;
 $('download').href=`data/returns_${variant}.csv`;
}
function nextMonth(date){const d=new Date(date+'T00:00:00Z');d.setUTCDate(1);d.setUTCMonth(d.getUTCMonth()+1);return d.toISOString().slice(0,7);}

function showMonth(row,tr){
 document.querySelectorAll('#months tr').forEach(r=>r.classList.remove('selected'));tr.classList.add('selected');
 const detail=$('month-detail');detail.hidden=false;detail.replaceChildren(el('strong',`${row.month} · Monthly targets before overlay`),el('div',`Signal: ${row.decision_date} close → next tradable session`));
 Object.entries({...row.weights,CASH:row.cash}).filter(([,w])=>w>0).sort((a,b)=>b[1]-a[1]).forEach(([t,w])=>detail.append(el('span',`${t}  ${fmt(w)}`,'weight-chip')));
}

function renderMonths(){
 const data=current().months;
 let rows=archive==='recent'?data.slice(-12):data.filter(r=>r.month.startsWith(archive));
 rows=[...rows].reverse();$('months').replaceChildren();$('month-detail').hidden=true;
 for(const row of rows){
  const tr=el('tr');tr.tabIndex=0;tr.setAttribute('aria-label',`Show allocation for ${row.month}`);
  tr.append(el('td',row.month),el('td',row.stock_picks.split(',').filter(Boolean).join(' · ')||'—'),el('td',row.etf_picks.split(',').filter(Boolean).join(' · ')||'—'));
  for(const key of ['dual_net','spy']){
   const value=row.returns?.[key];const td=el('td',value===undefined?'Upcoming':fmt(value,1),value===undefined?'upcoming':value>=0?'positive':'negative');tr.append(td);
  }
  tr.onclick=()=>showMonth(row,tr);tr.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();showMonth(row,tr);}};
  $('months').append(tr);
 }
}

function render(){renderMetrics();renderAllocation();renderMonths();drawChart();}

try{
 const response=await fetch('data/dashboard.json',{cache:'no-cache'});if(!response.ok)throw new Error(`Dashboard data HTTP ${response.status}`);
 dataset=await response.json();$('asof').textContent=dataset.data_asof;
 $('updated').textContent='Built '+new Date(dataset.generated_at_utc).toLocaleString('en-GB',{timeZone:'Asia/Bangkok'})+' ICT';
 $('cost').textContent=dataset.cost_bps_per_side;
 const years=[...new Set(current().months.map(r=>r.month.slice(0,4)))].reverse();
 for(const y of years){const option=el('option',y);option.value=y;$('archive-year').append(option);}
 dataset.methodology.forEach(text=>$('method-list').append(el('li',text)));
 $('provenance').textContent=`Strategy ${dataset.manifest.strategy_id} · config ${dataset.manifest.config_hash.slice(0,12)} · membership snapshot ${dataset.manifest.membership_asof} · all historical periods are seen.`;
 document.querySelectorAll('[data-variant]').forEach(button=>button.onclick=()=>{
  variant=button.dataset.variant;document.querySelectorAll('[data-variant]').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',String(b===button));});render();
 });
 document.querySelectorAll('[data-range]').forEach(button=>button.onclick=()=>{
  range=button.dataset.range;document.querySelectorAll('[data-range]').forEach(b=>b.classList.toggle('active',b===button));renderMetrics();drawChart();
 });
 $('archive-year').onchange=e=>{archive=e.target.value;renderMonths();};
 ['show-gross','show-stress','log-scale'].forEach(id=>$(id).onchange=drawChart);
 new ResizeObserver(()=>drawChart()).observe($('chart'));
 render();
 const healthResponse=await fetch('data/health.json',{cache:'no-cache'});
 const health=healthResponse.ok?await healthResponse.json():null;
 const ageDays=(Date.now()-Date.parse(dataset.data_asof+'T00:00:00Z'))/864e5;
 const stale=ageDays>4;
 if(health?.status==='blocked'||stale){$('health').classList.add('warning');$('health').textContent=stale?'Stale data · check update job':'Update blocked · last valid data';notice('ข้อมูลล่าสุดยังไม่ผ่าน freshness gate: กราฟและน้ำหนักด้านล่างเป็นชุดสำเร็จครั้งก่อน กรุณาตรวจวันข้อมูลและหน้า Actions');}
 else $('health').textContent=`${health?.fresh_symbols||'All'} source tickers checked · daily EOD`;
}catch(error){notice('โหลดข้อมูลไม่สำเร็จ: '+error.message+' · เปิดผ่าน HTTP/GitHub Pages แทน file://');$('health').textContent='Data unavailable';$('health').classList.add('warning');}
