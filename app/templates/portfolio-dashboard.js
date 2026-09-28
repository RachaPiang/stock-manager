// Presentation only. All portfolio money calculations come from Python.
const portfolio=data.portfolio;
const dollars=v=>v==null?'—':'$'+number(v);
const signedMoney=v=>v==null?'—':(v>=0?'+':'−')+'$'+number(Math.abs(v));
const savedControls=['portfolio-series','portfolio-range','allocation-view','holdings-sort'];
function saveView(){try{globalThis.sessionStorage?.setItem('stock-manager-portfolio-view',JSON.stringify(Object.fromEntries([...savedControls.map(id=>[id,$(id).value]),['auto',$('portfolio-auto-refresh').checked]])));}catch{}}
function openHolding(symbol){
 const stock=data.stocks.find(s=>s.symbol===symbol);if(!stock)return;
 selected=stock;panOffset=0;viewCount=null;$('stock-details').open=true;
 renderSelected();renderCards();$('chart-panel').scrollIntoView?.({behavior:'smooth',block:'start'});
}
function renderHoldings(){
 if(!portfolio)return;$('holdings-rows').replaceChildren();
 const rows=[...portfolio.holdings],sort=$('holdings-sort').value;
 rows.sort(sort==='symbol'?(a,b)=>a.symbol.localeCompare(b.symbol):sort==='gain'?(a,b)=>(b.live_gain_usd??-Infinity)-(a.live_gain_usd??-Infinity):(a,b)=>(b.live_value_usd??-Infinity)-(a.live_value_usd??-Infinity));
 for(const h of rows){
  const row=element('tr'),first=element('td'),link=element('button','holding-link',h.symbol);link.type='button';link.onclick=()=>openHolding(h.symbol);first.append(link);
  if(h.live_stale)first.append(element('small','muted',' · ข้อมูลเก่า'));
  row.append(first,element('td','',h.quantity==null?'—':Number(h.quantity).toLocaleString('en-US',{maximumFractionDigits:8})),element('td','',number(h.live_price)),element('td','',number(h.live_value_usd)));
  const weight=element('td');if(h.live_weight_pct!=null){const track=element('span','holding-weight'),fill=element('span');fill.style.width=h.live_weight_pct+'%';track.append(fill);weight.append(track,document.createTextNode(number(h.live_weight_pct)+'%'));}else weight.textContent='—';
  row.append(weight,element('td',h.live_gain_usd==null?'muted':h.live_gain_usd<0?'down':'up',signedMoney(h.live_gain_usd)),element('td',h.live_gain_pct==null?'muted':h.live_gain_pct<0?'down':'up',pct(h.live_gain_pct)),element('td','',h.cost_reliable===false?'รอยืนยัน':number(h.estimated_cost_usd)));
  row.setAttribute('title',h.live_as_of?'ราคา ณ '+stamp(h.live_as_of):'ยังไม่มีราคา');$('holdings-rows').append(row);
 }
}
function portfolioSeries(){
 if($('portfolio-series').value==='benchmark')return data.benchmark?.ranges?.[$('portfolio-range').value]||[];
 const simulation=$('portfolio-series').value==='simulation';
 let values=simulation?($('portfolio-range').value==='1d'?(data.portfolio_intraday||[]).map(p=>({...p,day:new Date(new Date(p.day).getTime()+300000).toISOString()})):(data.portfolio_history||[])):(data.portfolio_observations||[]);
 const latest=values.at(-1);if(!latest)return [];
 const last=new Date(latest.day),key=$('portfolio-range').value;
 if(key==='1d'){
  const session=d=>new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(d));
  return values.filter(p=>session(p.day)===session(latest.day));
 }
 const days={'1w':7,'1m':31,'3m':93,'6m':186,'1y':366,'5y':1827,'10y':3653}[key];
 return days?values.filter(p=>new Date(p.day).getTime()>=last.getTime()-days*86400000):values;
}
let portfolioInspect=()=>{},portfolioExpanded=false;
function renderPortfolioChart(){
 const svg=$('portfolio-chart'),values=portfolioSeries(),simulation=$('portfolio-series').value==='simulation';
 const comparison=$('portfolio-series').value==='benchmark';
 const measured=svg.getBoundingClientRect?.().width||960,W=Math.max(280,measured),H=300,L=W<420?66:85,R=14,T=25,B=35;
 svg.setAttribute('viewBox',`0 0 ${W} ${H}`);svg.replaceChildren();
 svg.onpointermove=null;svg.onpointerdown=null;svg.onpointerleave=null;svg.onkeydown=null;portfolioInspect=()=>{};
 $('portfolio-point').disabled=values.length<1;
 $('portfolio-chart-caption').textContent=simulation?'สมมติถือจำนวนหุ้นปัจจุบันเท่าเดิมตลอดช่วง':'มูลค่าจากราคาที่บันทึกครบทุกหุ้น ณ เวลาเดียวกัน';
 $('portfolio-history-note').textContent=simulation?'กราฟจำลอง: ใช้จำนวนหุ้นปัจจุบันคูณราคาย้อนหลัง ไม่ใช่ประวัติเงินจริงหรือผลตอบแทน DCA ของคุณ':'ติดตามตั้งแต่บันทึกยอดหุ้น · ยึดจำนวนหุ้นที่คุณแจ้ง ณ วันนั้นจนกว่าจะอัปเดตยอด · ข้ามรอบที่ราคาไม่ครบทุกตัว';
 const all=simulation?(data.portfolio_history||[]):(data.portfolio_observations||[]);
 if(all.length&&!simulation)$('portfolio-history-note').textContent+=' · เริ่ม '+stamp(all[0].day);
 if(comparison){
  $('portfolio-chart-caption').textContent='พอร์ตจำลอง (เขียว) เทียบ S&P 500 (ทอง) · เริ่มพร้อมกันที่ 0%';
  $('portfolio-history-note').textContent='ราคาปิดเฉพาะวันที่มีครบทั้งสองฝั่ง · จำนวนหุ้นปัจจุบันคงที่ ไม่ใช่ผลตอบแทน DCA จริง · ไม่รวมปันผล เงินเข้าออก FX และค่าธรรมเนียม · S&P Dow Jones Indices ผ่าน FRED ณ '+(data.benchmark?.as_of||'ยังไม่มีข้อมูล');
 }
 $('portfolio-range-result').textContent='';
 if(!values.length){
  svg.append(svgElement('text',{x:W/2,y:H/2,'text-anchor':'middle',fill:'#61758a','font-size':14},portfolio?(portfolio.live?.complete?'ยังไม่มีจุดกราฟในช่วงที่เลือก':'รอราคาครบทุกหุ้นเพื่อวาดกราฟ'):'ยังไม่มีข้อมูลพอร์ต'));
  $('portfolio-hover').textContent=comparison?'ต้องมีราคาปิดตรงกันอย่างน้อย 2 วัน · เลือก 1 สัปดาห์ขึ้นไป และรออัปเดตข้อมูล S&P 500':portfolio?'กราฟจะเพิ่มเมื่อมีรอบตรวจที่ข้อมูลครบ · ดูแนวโน้มระยะยาวได้ในโหมดจำลอง':'เปิดใช้ข้อมูลพอร์ตเพื่อดูมูลค่ารวม';return;
 }
 const vals=values.map(p=>p.value_usd),bounds=comparison?vals.concat(values.map(p=>p.benchmark_pct)):vals,minimum=Math.min(...bounds),maximum=Math.max(...bounds),padding=Math.max((maximum-minimum)*.12,maximum*.002,1),lo=minimum-padding,hi=maximum+padding;
 const x=i=>L+(values.length===1?.5:i/(values.length-1))*(W-L-R),y=v=>T+(hi-v)/(hi-lo)*(H-T-B);
 const color=comparison||vals.at(-1)>=vals[0]?'#157c83':'#b74744';
 svg.append(svgElement('text',{x:L,y:14,fill:'#61758a','font-size':12},comparison?'เปลี่ยนแปลง (%)':'มูลค่า (USD)'));
 for(let i=0;i<4;i++){const v=lo+(hi-lo)*i/3,yy=y(v);svg.append(svgElement('line',{x1:L,y1:yy,x2:W-R,y2:yy,stroke:'#e2eaf0'}),svgElement('text',{x:L-9,y:yy+4,'text-anchor':'end',fill:'#61758a','font-size':12},number(v)));}
 const tickCount=Math.min(values.length,W<500?3:5),seen=new Set();
 for(let t=0;t<tickCount;t++){
  const i=tickCount===1?0:Math.round(t*(values.length-1)/(tickCount-1));if(seen.has(i))continue;seen.add(i);
  const time=values[i].day,label=time.includes('T')?new Intl.DateTimeFormat('th-TH',{timeZone:'Asia/Bangkok',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(time)):day(time);
  svg.append(svgElement('text',{x:x(i),y:H-8,'text-anchor':t===0?'start':t===tickCount-1?'end':'middle',fill:'#61758a','font-size':12},label));
 }
 const line=vals.map((v,i)=>(i?'L':'M')+x(i).toFixed(2)+','+y(v).toFixed(2)).join(' ');
 if(comparison){const benchmarkLine=values.map((p,i)=>(i?'L':'M')+x(i).toFixed(2)+','+y(p.benchmark_pct).toFixed(2)).join(' ');svg.append(svgElement('path',{d:benchmarkLine,fill:'none',stroke:'#ac7819','stroke-width':2.5,'data-benchmark-line':'true'}));}
 if(values.length>1){svg.append(svgElement('path',{d:line+` L${x(vals.length-1)},${H-B} L${x(0)},${H-B} Z`,fill:color,'fill-opacity':.08}));svg.append(svgElement('path',{d:line,fill:'none',stroke:color,'stroke-width':2.5,'data-portfolio-line':'true'}));}
 const guide=svgElement('line',{y1:T,y2:H-B,stroke:color,'stroke-dasharray':'4 4','pointer-events':'none'}),dot=svgElement('circle',{r:5,fill:color,stroke:'#fff','stroke-width':2,'pointer-events':'none'});
 svg.append(guide,dot,svgElement('rect',{x:L,y:T,width:W-L-R,height:H-T-B,fill:'transparent','pointer-events':'all'}));
 let pinned=false,current=values.length-1;
 function show(i){current=Math.max(0,Math.min(values.length-1,i));const p=values[current];guide.setAttribute('x1',x(current));guide.setAttribute('x2',x(current));dot.setAttribute('cx',x(current));dot.setAttribute('cy',y(p.value_usd));
  $('portfolio-hover').textContent=(p.day.includes('T')?stamp(p.day):day(p.day))+' · '+dollars(p.value_usd)+' USD'+(simulation?' · จำลอง':p.cost_usd!=null?' · กำไรประมาณ '+signedMoney(p.value_usd-p.cost_usd):'')+(pinned?' · ตรึงแล้ว':'');
  if(comparison)$('portfolio-hover').textContent=day(p.day)+' · พอร์ตจำลอง '+pct(p.value_usd)+' · S&P 500 '+pct(p.benchmark_pct)+' · ต่างกัน '+number(p.value_usd-p.benchmark_pct)+' จุดเปอร์เซ็นต์'+(pinned?' · ตรึงแล้ว':'');
  $('portfolio-point').value=String(current);$('portfolio-point').setAttribute('aria-valuetext',$('portfolio-hover').textContent);
 }
 function indexAt(e){const matrix=svg.getScreenCTM?.();if(!matrix)return current;const point=svg.createSVGPoint();point.x=e.clientX;point.y=e.clientY;return Math.round((point.matrixTransform(matrix.inverse()).x-L)/(W-L-R)*(values.length-1));}
 svg.onpointermove=e=>{if(!pinned)show(indexAt(e));};svg.onpointerdown=e=>{pinned=!pinned;show(indexAt(e));};svg.onpointerleave=()=>{if(!pinned)show(values.length-1);};
 svg.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End','Escape'].includes(e.key)){e.preventDefault();pinned=e.key!=='Escape';show(e.key==='Home'?0:e.key==='End'||e.key==='Escape'?values.length-1:current+(e.key==='ArrowLeft'?-1:1));}};
 portfolioInspect=i=>{pinned=true;show(i);};$('portfolio-point').max=String(values.length-1);show(current);
 $('portfolio-range-result').textContent=values.length>1?'เปลี่ยนแปลงในช่วง '+signedMoney(vals.at(-1)-vals[0])+' ('+pct((vals.at(-1)/vals[0]-1)*100)+') · '+values.length+' จุด · แกนราคาไม่เริ่มที่ศูนย์'+(simulation?' · ไม่ใช่ผลตอบแทนพอร์ตจริง':' · ยังไม่รวมผลจากเงินเข้าออก'): 'เริ่มเก็บข้อมูลแล้ว 1 จุด · กราฟจะต่อเส้นเมื่อมีรอบใหม่';
 if(comparison)$('portfolio-range-result').textContent='ช่วง '+day(values[0].day)+' ถึง '+day(values.at(-1).day)+' · พอร์ตจำลอง '+pct(vals.at(-1))+' / S&P 500 '+pct(values.at(-1).benchmark_pct)+' · ไม่ใช่ผลตอบแทนจริงที่คุณได้รับ';
}
function renderPortfolioOverview(){
 const live=portfolio?.live;
 if(!portfolio){$('portfolio-quality').textContent=data.mock?'โหมดทดลอง · ยังไม่นำจำนวนหุ้นจริงมารวมกับราคาจำลอง':'ยังไม่มีข้อมูลจำนวนหุ้น';renderPortfolioChart();return;}
 $('portfolio-total').textContent=live?.complete?dollars(live.total_usd):'—';
 $('portfolio-cost').textContent=live?.cost_complete===false?'รอยืนยัน':dollars(portfolio.estimated_cost_total_usd);
 $('portfolio-gain').textContent=live?.complete?signedMoney(live.gain_usd):'—';
 $('portfolio-gain').className=live?.gain_usd<0?'down':'up';
 $('portfolio-gain-pct').textContent=live?.cost_complete===false?'รอยืนยันต้นทุนจาก Dime หลัง DCA':live?.complete?pct(live.gain_pct)+' เทียบต้นทุนหุ้นที่ถือ':'รอข้อมูลครบทุกหุ้น';
 $('portfolio-day').textContent=signedMoney(live?.day_change_usd);$('portfolio-day').className=live?.day_change_usd<0?'down':'up';
 $('portfolio-day-pct').textContent=live?.day_change_pct!=null?pct(live.day_change_pct)+' · วันตลาด '+day(live.session_date):'รอราคาและราคาปิดอ้างอิงครบวันเดียวกัน';
 $('portfolio-cost-note').textContent=live?.cost_complete===false?'มีการเปลี่ยนยอดหุ้นแต่ยังไม่ยืนยันต้นทุนรวม · มูลค่าตลาดยังอัปเดตตามปกติ':live?.cost_estimated?'ต้นทุนเริ่มต้นคำนวณจากกำไร % ที่เคยแจ้ง · กรอกจาก Dime แล้วจะแม่นขึ้น':'ต้นทุนที่คุณบันทึกจากโบรกเกอร์';
 $('portfolio-quality').textContent=live?.complete?'ราคาครบ '+portfolio.holdings.length+' หุ้น · '+stamp(live.oldest_quote_as_of)+(live.mixed_times?' ถึง '+stamp(live.newest_quote_as_of)+' · เวลาของแต่ละหุ้นต่างกัน':'')+(live.stale?' · มีข้อมูลเก่า ควรตรวจรอบอัปเดต':''):(live?.reason||'รอราคาครบทุกหุ้น')+' · ภาพพอร์ตเดิม '+dollars(portfolio.total_usd)+' ณ '+day(portfolio.as_of);
 $('portfolio-quality').className='portfolio-quality'+(!live?.complete||live.stale?' warning':'');
 $('portfolio-dca').textContent='ออมเดือนละ '+number(portfolio.dca.monthly_total)+' บาท';
 $('portfolio-dca-detail').textContent='วันที่ '+portfolio.dca.day+' · ตัวละ '+number(portfolio.dca.per_stock)+' บาท · '+portfolio.holdings.length+' หุ้น';
 const checks=[['จำนวนหุ้น',portfolio.holdings.filter(h=>h.quantity!=null).length+' / '+portfolio.holdings.length+' ตัว'],['ต้นทุนจากโบรกเกอร์',portfolio.holdings.filter(h=>h.cost_source==='user_reported').length+' / '+portfolio.holdings.length+' ตัว'],['เชื่อมรายการ DCA อัตโนมัติ','ยังไม่เชื่อม · บันทึกยอดหลังซื้อ'],['รายงานกับราคา','เปิดหน้านี้ไม่ดึงราคาเพิ่ม · รอรอบตรวจแล้วโหลดหน้าใหม่']];
 $('portfolio-checks').replaceChildren();for(const [label,value] of checks){const row=element('div');row.append(element('span','',label),element('span','',value));$('portfolio-checks').append(row);}
 renderHoldings();renderPortfolioChart();
}
$('portfolio-series').value='observed';$('portfolio-range').value='all';
$('portfolio-series').onchange=()=>{saveView();renderPortfolioChart();};$('portfolio-range').onchange=()=>{saveView();renderPortfolioChart();};
$('portfolio-point').oninput=e=>portfolioInspect(Number(e.target.value));
$('holdings-sort').onchange=()=>{saveView();renderHoldings();};$('allocation-view').onchange=()=>{saveView();if(portfolio)renderAllocation(portfolio);};
$('reload-portfolio').onclick=()=>globalThis.location?.reload();
$('manage-portfolio-link').onclick=()=>{$('manage-portfolio').open=true;};
function expandPortfolio(value){portfolioExpanded=value;$('portfolio-chart-panel').classList.toggle('full-chart',value);$('portfolio-fullscreen').textContent=value?'✕ ปิดเต็มจอ':'⛶ เต็มจอ';$('portfolio-fullscreen').setAttribute('aria-pressed',String(value));renderPortfolioChart();}
$('portfolio-fullscreen').onclick=async()=>{if(portfolioExpanded){if(document.fullscreenElement)await document.exitFullscreen?.();expandPortfolio(false);return;}expandPortfolio(true);try{await $('portfolio-chart-panel').requestFullscreen?.();}catch{}};
document.addEventListener?.('keydown',e=>{if(e.key==='Escape'&&portfolioExpanded)expandPortfolio(false);});
document.addEventListener?.('fullscreenchange',()=>{if(!document.fullscreenElement&&portfolioExpanded)expandPortfolio(false);});
if(typeof ResizeObserver!=='undefined')new ResizeObserver(()=>renderPortfolioChart()).observe($('portfolio-chart'));
try{const saved=JSON.parse(globalThis.sessionStorage?.getItem('stock-manager-portfolio-view')||'null');if(saved){for(const id of savedControls){if(saved[id])$(id).value=saved[id];}$('portfolio-auto-refresh').checked=saved.auto!==false;}}catch{}
$('portfolio-auto-refresh').onchange=saveView;
globalThis.setInterval?.(()=>{if($('portfolio-auto-refresh').checked&&!document.hidden&&!portfolioExpanded&&!expanded){saveView();globalThis.location?.reload();}},300000);
if(portfolio)renderAllocation(portfolio);
renderPortfolioOverview();
