'use strict';
const $=id=>document.getElementById(id);
const apiPath=path=>(location.protocol==='file:'?'http://127.0.0.1:8766':'')+path;
const fmt=(n,d=2)=>n==null?'—':Number(n).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const pct=n=>n==null?'—':`${n>0?'+':''}${fmt(n)}%`, money=n=>n==null?'—':`$${fmt(n)}`;
const timestamp=ts=>ts?new Date(ts*1000).toLocaleString('ko-KR'):'—';
const shortDate=ts=>ts?new Date(ts*1000).toLocaleDateString('ko-KR',{month:'2-digit',day:'2-digit'}):'—';
const rangeName=r=>({'6mo':'6개월','1y':'1년','2y':'2년','5y':'5년','10y':'10년',max:'전체'})[r]||r;
let state=null,symbols=[],info={},busy=false,structureKey='',dialogRevision=null;
let portfolioSymbols=[],inputsKey='';
let nativeProperties={},nativeVersion=0,appliedNativeVersion=0,nativeBusy=false,nativePresetError='';
function node(tag,className,text){const e=document.createElement(tag);if(className)e.className=className;if(text!=null)e.textContent=text;return e;}
function krwEquivalent(value){const rate=state?.fx?.rate;return value==null||!Number.isFinite(rate)||rate<=0?'원화 환산 대기':`약 ${fmt(value*rate,0)}원`;}
function rebuild(config){
 const key=JSON.stringify([config.assets.map(a=>[a.symbol,a.name,a.category,a.color]),config.indicators]);
 symbols=config.assets.map(a=>a.symbol);info=Object.fromEntries(config.assets.map(a=>[a.symbol,a]));
 $('boardTitle').textContent=config.title;document.title=`${config.title} · 시세와 자산 배분`;$('assetCount').textContent=symbols.length;
 if(key===structureKey)return;
 if($('settings').open){$('settings').close();}
 structureKey=key;inputsKey='';$('cards').replaceChildren();$('inputs').replaceChildren();$('cards').style.setProperty('--columns',Math.min(4,symbols.length));
 for(const s of symbols){
  const a=info[s],card=node('article','card');card.style.setProperty('--asset-color',a.color);card.title=a.name;
  card.innerHTML='<div class="card-top"><span class="ticker"><i class="ticker-mark"></i><span class="ticker-text"></span></span><span class="category"></span></div><div class="asset-name"></div><div class="price-row"><strong class="price">—</strong><span class="change">—</span></div><svg class="chart" viewBox="0 0 300 70" preserveAspectRatio="none" role="img"><path class="chart-grid" d="M0 65H300 M0 35H300"/><path class="spark" fill="none" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg><div class="metrics"><div><span class="rsi-label"></span><strong class="rsi">—</strong></div><div><span class="mdd-label"></span><strong class="mdd">—</strong></div><div><span>고점 대비</span><strong class="drawdown">—</strong></div></div><div class="card-note">일별 시세 이력 대기 중</div>';
  card.querySelector('.price-row').after(node('div','price-krw'));
  card.querySelector('.ticker-text').textContent=s;card.querySelector('.category').textContent=a.category;card.querySelector('.asset-name').textContent=a.name;
  card.querySelector('.rsi-label').textContent=`RSI · ${config.indicators.rsi_period}`;card.querySelector('.mdd-label').textContent=`MDD · ${rangeName(config.indicators.history_range)}`;
  card.querySelector('.chart').setAttribute('aria-label',`${s} ${rangeName(config.indicators.history_range)} 조정 종가 차트`);card.querySelector('.spark').setAttribute('stroke',a.color);card.id=`card-${s}`;$('cards').append(card);
  const row=node('div','input-row'),label=node('b','',s);label.style.color=a.color;row.append(label);
  for(const [prefix,description]of[['qty','보유 수량'],['target','목표 비중']]){const input=node('input');input.name=`${prefix}-${s}`;input.type='number';input.min='0';input.step='any';input.setAttribute('aria-label',`${s} ${description}`);if(prefix==='qty')input.required=true;else input.max='100';row.append(input);}$('inputs').append(row);
 }
}
function render(){
 renderDipPlan();
 renderManualAccounts();
 rebuild(state.config);let bad=0,fetched=[],stops=[],cursor=0;const config=state.config,p=state.portfolio;
 for(const position of state.account.positions||[])if(!info[position.symbol])info[position.symbol]={symbol:position.symbol,name:position.name,category:'계좌 보유',color:'#c9a45a'};
 portfolioSymbols=[...new Set([...symbols,...Object.keys(state.settings.quantities)])];
 for(const s of portfolioSymbols)if(!info[s])info[s]={symbol:s,name:s,category:'계좌 보유',color:'#c9a45a'};
 const nextInputsKey=JSON.stringify(portfolioSymbols);
 if(inputsKey!==nextInputsKey){
  if($('settings').open)$('settings').close();
  $('inputs').replaceChildren();
  for(const s of portfolioSymbols){const row=node('div','input-row'),label=node('b','',s);label.style.color=info[s].color;row.append(label);for(const[prefix,description]of[['qty','보유 수량'],['target','목표 비중']]){const input=node('input');input.name=`${prefix}-${s}`;input.type='number';input.min='0';input.step='any';input.setAttribute('aria-label',`${s} ${description}`);if(prefix==='qty')input.required=true;else input.max='100';row.append(input);}$('inputs').append(row);}
  inputsKey=nextInputsKey;
 }
 for(const s of symbols){
  const q=state.quotes[s]||{},card=$(`card-${s}`);card.querySelector('.price').textContent=money(q.price);
  card.querySelector('.price-krw').textContent=krwEquivalent(q.price);
  const change=card.querySelector('.change');change.textContent=pct(q.change);change.className=`change ${q.change>0?'positive':q.change<0?'negative':''}`;change.title='전일 종가 대비 등락률';
  card.querySelector('.rsi').textContent=fmt(q.rsi,1);card.querySelector('.mdd').textContent=pct(q.mdd);card.querySelector('.drawdown').textContent=pct(q.drawdown);
  const stale=q.error||(q.fetchedAt&&Date.now()/1000-q.fetchedAt>Math.max(300,config.refresh_seconds*2.5));if(stale||!q.price)bad++;if(q.fetchedAt)fetched.push(q.fetchedAt);
  const note=card.querySelector('.card-note');note.textContent=stale?(q.error||'시세 갱신 지연 · 이전 값'):q.price?`시세 ${shortDate(q.quoteAt)} · 지표 ${shortDate(q.indicatorAt)}`:'일별 시세 이력 대기 중';note.title=`시세 ${timestamp(q.quoteAt)} / 완료 일봉 ${timestamp(q.indicatorAt)} / 조회 ${timestamp(q.fetchedAt)}`;
  const path=card.querySelector('.spark');if(q.history?.length>1){const a=q.history,min=Math.min(...a),span=Math.max(...a)-min||1;path.setAttribute('d',a.map((v,i)=>`${i?'L':'M'}${(i/(a.length-1)*300).toFixed(2)},${(65-(v-min)/span*60).toFixed(2)}`).join(' '));}else path.removeAttribute('d');
 }
 $('connection').textContent=bad?`${bad}개 종목 시세 대기·지연`:`시세 수신 · ${config.refresh_seconds}초 간격`;$('connection').className=`feed-status ${bad?'warning':'good'}`;
 $('account').textContent=state.account.label;$('account').className=`feed-status ${state.account.connected&&!state.account.error?'good':''}`;
 const configMessage=nativePresetError||(state.configError?`${state.configError} 마지막 정상 설정을 표시합니다.`:'');$('configError').hidden=!configMessage;$('configError').textContent=configMessage;
 $('updated').textContent=fetched.length?`Yahoo Finance · 조회 ${new Date(Math.min(...fetched)*1000).toLocaleTimeString('ko-KR',{hour12:false})}`:'Yahoo Finance · 시세 대기 중';
 $('method').textContent=`RSI ${config.indicators.rsi_period} · MDD ${rangeName(config.indicators.history_range)} · 조정 완료 일봉 · 시세 지연 가능`;
 $('total').textContent=money(p.total);$('cash').textContent=money(state.settings.cash);$('contribution').textContent=money(state.settings.contribution);$('targetCash').textContent=money(p.targetCash);
 $('totalKrw').textContent=krwEquivalent(p.total);$('cash').append(node('span','cell-sub',krwEquivalent(state.settings.cash)));
 const fx=state.fx,fxStale=fx?.error||(fx?.fetchedAt&&Date.now()/1000-fx.fetchedAt>Math.max(300,config.refresh_seconds*2.5));
 $('fxNote').textContent=fx?.rate?`1 USD ≈ ${fmt(fx.rate)}원 · ${fxStale?'환율 갱신 지연 · ':''}${fx.source} · 기준 ${timestamp(fx.quoteAt)} · 조회 ${timestamp(fx.fetchedAt)}`:'USD/KRW 환율 조회 대기 중';
 const won=state.account.cashByCurrency?.KRW;$('wonCashRow').hidden=won==null;$('wonCash').textContent=won==null?'—':`${fmt(won,0)}원`;
 const domestic=state.account.domestic;$('domesticSummary').hidden=!state.account.connected||!domestic;
 for(const[id,key]of[['domesticCash','cashToday'],['domesticWithdrawable','withdrawableCash'],['domesticD2','cashD2'],['domesticNet','accountNetAssets']])$(id).textContent=domestic?.available&&domestic[key]!=null?`${fmt(domestic[key],0)}원`:'—';
 $('domesticNote').textContent=domestic?.available?'같은 연결계좌의 참고 조회값입니다. 위 예수금·달러 자산과 중복 합산하지 않습니다.':(domestic?.error||'원화 계좌 요약 조회 대기 중');
 $('maxGap').textContent=p.ready?`${fmt(p.maxGap,1)}%p`:'—';$('driftStatus').textContent=p.ready?p.needsRebalance?'조정 조건 충족':'허용 오차 이내':'목표 비중 미설정';$('rows').replaceChildren();
 for(const r of p.rows||[]){
  if(r.weight>0){stops.push(`${info[r.symbol].color} ${cursor}% ${cursor+r.weight}%`);cursor+=r.weight;}
  const tr=node('tr');tr.style.setProperty('--asset-color',info[r.symbol].color);const symbol=node('td');symbol.append(node('i','ticker-mark'),node('span','position-ticker',r.symbol));tr.append(symbol);
  const value=node('td','cell-main',money(r.value));value.append(node('span','cell-sub',krwEquivalent(r.value)),node('span','cell-sub',`${fmt(r.quantity,4)}주`));tr.append(value);
  const weight=node('td','weight-cell'),labels=node('div','weight-label');labels.append(node('span','',`${fmt(r.weight,1)}%`),node('span','target',`/ ${r.target==null?'미설정':fmt(r.target,1)+'%'}`));
  const track=node('div','weight-track'),actual=node('span','actual');actual.style.width=`${r.weight}%`;track.append(actual);if(r.target!=null){const mark=node('i','marker');mark.style.left=`${r.target}%`;track.append(mark);}weight.append(labels,track);tr.append(weight);
  tr.append(node('td','cell-main',r.gap==null?'—':`${r.gap>0?'+':''}${fmt(r.gap,1)}%p`));
  const action=r.delta==null?'—':Math.abs(r.delta)<.005?'유지':r.delta>0?'매수':'매도',trade=node('td',`cell-main ${r.delta>.005?'positive':r.delta<-.005?'negative':''}`);trade.append(node('span','trade-badge',action));if(r.delta!=null)trade.append(document.createTextNode(money(Math.abs(r.delta))),node('span','cell-sub',`${fmt(Math.abs(r.shares),4)}주`));tr.append(trade);$('rows').append(tr);
 }
 if(cursor<100)stops.push(`#71685b ${cursor}% 100%`);$('allocation').style.background=stops.length?`conic-gradient(${stops.join(',')})`:'#30291f';$('allocation').setAttribute('aria-label',`현재 자산 비중: ${(p.rows||[]).map(r=>`${r.symbol} ${fmt(r.weight,1)}%`).join(', ')}; 나머지는 현금`);
 $('portfolioStatus').textContent=p.ready?p.needsRebalance?`최대 비중 차이 ${fmt(p.maxGap,1)}%p · 허용 오차 ${fmt(state.settings.threshold,1)}%p · 추가 투입금 반영`:`허용 오차 ${fmt(state.settings.threshold,1)}%p 이내 · 현재 비중 유지`:p.reason;
 if(p.ready&&bad)$('portfolioStatus').textContent+=' · 시세 지연으로 이전 값이 포함될 수 있습니다.';
 $('scopeNote').textContent=state.account.connected?`실제 USD 보유 자산 기준 · 원화 예수금은 별도 표시${state.account.excluded?` · USD 이외 보유 ${state.account.excluded}개 항목 미반영`:''} · 수수료·세금 미반영`:`설정한 ${symbols.length}개 종목 + USD 현금 기준 · 수수료·세금 미반영 · 소수점 이론 수량`;
}
function renderDipPlan(){
 let panel=$('dipPlan');
 if(!panel){panel=node('section','dip-plan');panel.id='dipPlan';panel.setAttribute('aria-label','하락장 분할 매수 계획');$('cards').before(panel);}
 const p=state.dipPlan;panel.hidden=!p?.enabled;if(!p?.enabled)return;
 panel.replaceChildren();
 const heading=node('div','dip-heading');
 heading.append(node('strong','',`${p.symbol} 하락장 분할 매수`),node('span','',`대기자금 ${fmt(p.cashKrw,0)}원`));
 panel.append(heading,node('p','dip-status',p.message));
 if(p.ready){
  panel.append(node('p','dip-reference',`${p.cycle}회차 · 기준 고점 $${fmt(p.referenceClose)} (${shortDate(p.referenceAt)}) · 고점 대비 ${pct(p.drawdown)} · 완료 종가 ${shortDate(p.signalAt)}`));
  const steps=node('div','dip-steps');
  for(const row of p.rows){const step=node('div',`dip-step ${row.status}`);step.append(node('b','',`−${fmt(row.level,0)}%`),node('strong','',`${fmt(row.amountKrw,0)}원`),node('span','',({completed:'체결 기록됨',due:'매수 조건 충족',waiting:'대기'})[row.status]));steps.append(step);}
  panel.append(steps);
 }
 panel.append(node('p','dip-reference',`월 적립 유지 · 단계마다 남은 현금의 ${fmt(p.spendPercent,0)}% · 실제 체결은 상단 JSON 편집에서 기록`));
}
let manualRevision=null;
function renderManualAccounts(){
 const list=$('manualAccounts');list.replaceChildren();
 for(const a of state.manualAccounts||[]){
  const card=node('article','manual-account'),heading=node('div','manual-heading');
  heading.append(node('h3','',a.name),node('span','feed-status',`수기 · ${a.asOf||'기준일 미입력'}`));card.append(heading);
  const summary=node('div','manual-totals');summary.append(node('strong','',a.total==null?'미입력':`${fmt(a.total,0)}원`),node('span','',a.cash==null?'현금 미입력':`현금 ${fmt(a.cash,0)}원`));card.append(summary);
  if(a.rows.length){
   const wrap=node('div','manual-table-wrap'),table=node('table'),head=node('thead'),hr=node('tr');
   for(const label of['종목 / 수량','평가금액 / 비중','목표 / 조정'])hr.append(node('th','',label));head.append(hr);table.append(head);
   const body=node('tbody');
   for(const h of a.rows){
    const row=node('tr'),name=node('td','',h.name);name.append(node('span','cell-sub',`${h.symbol} · ${h.quantity==null?'수량 미입력':fmt(h.quantity,4)+'주'}`));
    const value=node('td','',`${fmt(h.value,0)}원`);value.append(node('span','cell-sub',h.weight==null?'비중 계산 대기':`${fmt(h.weight,1)}%`));
    const trade=node('td','',h.target_percent==null?'목표 미설정':`목표 ${fmt(h.target_percent,1)}%`);
    if(h.delta!=null){trade.append(node('span',`cell-sub ${h.delta>0?'positive':h.delta<0?'negative':''}`,Math.abs(h.delta)<.5?'유지':`${h.delta>0?'매수':'매도'} ${fmt(Math.abs(h.delta),0)}원`));if(h.shares!=null&&Math.abs(h.delta)>=.5)trade.append(node('span','cell-sub',`${fmt(Math.abs(h.shares),4)}주`));}
    row.append(name,value,trade);body.append(row);
   }
   table.append(body);wrap.append(table);card.append(wrap);
  }else card.append(node('p','form-note','보유내역 미입력 · 수기 계좌 편집에서 입력하세요.'));
  card.append(node('p','manual-note',a.ready?(a.needsRebalance?'목표 비중 조정 필요':'허용 오차 이내')+' · 입력한 평가금액 기준 · 수수료·세금 미반영':a.reason));list.append(card);
 }
}
$('manualEdit').onclick=()=>openConfigEditor($('manualEdit'));
$('closeManual').onclick=()=>$('manualDialog').close();
$('manualForm').onsubmit=async e=>{
 e.preventDefault();const button=$('manualSave');button.disabled=true;
 try{const manual_accounts=JSON.parse($('manualJson').value);const r=await fetch(apiPath('/api/manual-accounts'),{method:'POST',headers:{'Content-Type':'application/json','X-Session-Key':state.sessionKey},body:JSON.stringify({manual_accounts,configRevision:manualRevision})});if(!r.ok)throw Error((await r.json()).error);$('manualDialog').close();await poll();}
 catch(e){$('manualError').textContent=e.message||'수기 계좌를 저장하지 못했습니다.';}finally{button.disabled=false;}
};
async function poll(){if(busy)return;busy=true;try{const r=await fetch(apiPath('/api/state')),data=await r.json();if(!r.ok)throw Error(data.error);state=data;$('offline').hidden=true;render();}catch(e){$('offline').hidden=false;$('offline').textContent=e.message||'데이터 서비스 연결 끊김';$('connection').textContent='연결 끊김 · 마지막 수신 값';}finally{busy=false;if(window.parent!==window)window.parent.postMessage({type:'etf-board-ready'},'*');if(state)void applyNativeProperties();}}
$('settingsButton').onclick=()=>openConfigEditor($('settingsButton'));
async function openConfigEditor(button){
 if(!state)return;button.disabled=true;
 try{const r=await fetch(apiPath('/api/edit-config'),{method:'POST',headers:{'Content-Type':'application/json','X-Session-Key':state.sessionKey},body:'{}'});if(!r.ok)throw Error((await r.json()).error);$('editorStatus').textContent='적용 중인 JSON을 편집기로 열었습니다. 저장하면 화면에 자동 반영됩니다.';}
 catch(e){$('editorStatus').textContent=e.message||'편집기를 열지 못했습니다.';}finally{button.disabled=false;}
}
$('closeSettings').onclick=()=>$('settings').close();
function updateSum(){const f=$('settingsForm');let sum=Number(f.elements.cash_target.value),complete=true;for(const s of portfolioSymbols){const v=f.elements[`target-${s}`]?.value;if((v==null||v==='')&&state.settings.quantities[s]>0)complete=false;sum+=Number(v||0);}$('targetSum').textContent=`종목 + 현금 목표 합계 ${fmt(sum)}%${complete&&Math.abs(sum-100)<1e-6?' · 계산 가능':' · 실제 보유 종목의 목표와 합계 100%가 필요합니다.'}`;}
$('settingsForm').addEventListener('input',updateSum);
$('settingsForm').onsubmit=async e=>{e.preventDefault();const f=e.target,data={quantities:{},targets:{},configRevision:dialogRevision};for(const s of portfolioSymbols){data.quantities[s]=Number(f.elements[`qty-${s}`].value);data.targets[s]=f.elements[`target-${s}`].value===''?null:Number(f.elements[`target-${s}`].value);}for(const key of['cash','contribution','cash_target','threshold'])data[key]=Number(f.elements[key].value);try{const r=await fetch(apiPath('/api/settings'),{method:'POST',headers:{'Content-Type':'application/json','X-Session-Key':state.sessionKey},body:JSON.stringify(data)});if(!r.ok)throw Error((await r.json()).error);$('settings').close();await poll();}catch(e){$('saveError').textContent=e.message||'설정을 저장하지 못했습니다.';}};
function clock(){const now=new Date();$('clock').textContent=now.toLocaleTimeString('ko-KR',{hour12:false,hour:'2-digit',minute:'2-digit'});$('today').textContent=now.toLocaleDateString('ko-KR',{month:'long',day:'numeric',weekday:'short'});}
async function applyNativeProperties(){
 if(!state||nativeBusy||nativeVersion===appliedNativeVersion)return;
 nativeBusy=true;const version=nativeVersion,properties=JSON.parse(JSON.stringify(nativeProperties));
 try{
  const fresh=await(await fetch(apiPath('/api/state'))).json();
  const r=await fetch(apiPath('/api/preset'),{method:'POST',headers:{'Content-Type':'application/json','X-Session-Key':fresh.sessionKey},body:JSON.stringify({properties,configRevision:fresh.configRevision})});
  if(!r.ok)throw Error((await r.json()).error);
  nativePresetError='';
 }catch(e){nativePresetError=`Wallpaper Engine 프리셋: ${e.message}`;}
 finally{appliedNativeVersion=version;nativeBusy=false;await poll();}
}
function receiveNativeProperties(properties){
 let changed=false;
 for(const name of ['portfoliojson','kbconnect','kbprofile'])if(Object.hasOwn(properties,name)&&JSON.stringify(nativeProperties[name])!==JSON.stringify(properties[name])){nativeProperties[name]=properties[name];changed=true;}
 if(changed){nativeVersion++;void applyNativeProperties();}
}
window.wallpaperPropertyListener={applyUserProperties:receiveNativeProperties};
if(location.hash.startsWith('#we=')){
 try{receiveNativeProperties(JSON.parse(decodeURIComponent(location.hash.slice(4))));}catch(e){nativePresetError='Wallpaper Engine 설정을 읽지 못했습니다.';}
 history.replaceState(null,'',location.pathname+location.search);
}
document.querySelector('footer a[download]').href=apiPath('/api/config');
$('settingsButton').textContent='JSON 편집 ↗';$('manualEdit').textContent='수기 JSON 편집 ↗';
document.body.append(Object.assign(node('p','editor-status'),{id:'editorStatus',role:'status'}));
$('total').after(Object.assign(node('div','total-krw'),{id:'totalKrw'}));
document.querySelector('.balance-panel').append(Object.assign(node('p','fx-note'),{id:'fxNote'}));
clock();setInterval(clock,1000);poll();setInterval(poll,3000);
