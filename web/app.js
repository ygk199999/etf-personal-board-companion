'use strict';
const $=id=>document.getElementById(id);
const fmt=(n,d=2)=>n==null?'—':Number(n).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const pct=n=>n==null?'—':`${n>0?'+':''}${fmt(n)}%`, money=n=>n==null?'—':`$${fmt(n)}`;
const timestamp=ts=>ts?new Date(ts*1000).toLocaleString('ko-KR'):'—';
const shortDate=ts=>ts?new Date(ts*1000).toLocaleDateString('ko-KR',{month:'2-digit',day:'2-digit'}):'—';
const rangeName=r=>({'6mo':'6개월','1y':'1년','2y':'2년','5y':'5년','10y':'10년',max:'전체'})[r]||r;
let state=null,symbols=[],info={},busy=false,structureKey='',dialogRevision=null;
let portfolioSymbols=[],inputsKey='';
let nativeProperties={},nativeVersion=0,appliedNativeVersion=0,nativeBusy=false,nativePresetError='';
function node(tag,className,text){const e=document.createElement(tag);if(className)e.className=className;if(text!=null)e.textContent=text;return e;}
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
  card.querySelector('.ticker-text').textContent=s;card.querySelector('.category').textContent=a.category;card.querySelector('.asset-name').textContent=a.name;
  card.querySelector('.rsi-label').textContent=`RSI · ${config.indicators.rsi_period}`;card.querySelector('.mdd-label').textContent=`MDD · ${rangeName(config.indicators.history_range)}`;
  card.querySelector('.chart').setAttribute('aria-label',`${s} ${rangeName(config.indicators.history_range)} 조정 종가 차트`);card.querySelector('.spark').setAttribute('stroke',a.color);card.id=`card-${s}`;$('cards').append(card);
  const row=node('div','input-row'),label=node('b','',s);label.style.color=a.color;row.append(label);
  for(const [prefix,description]of[['qty','보유 수량'],['target','목표 비중']]){const input=node('input');input.name=`${prefix}-${s}`;input.type='number';input.min='0';input.step='any';input.setAttribute('aria-label',`${s} ${description}`);if(prefix==='qty')input.required=true;else input.max='100';row.append(input);}$('inputs').append(row);
 }
}
function render(){
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
 const won=state.account.cashByCurrency?.KRW;$('wonCashRow').hidden=won==null;$('wonCash').textContent=won==null?'—':`${fmt(won,0)}원`;
 const domestic=state.account.domestic;$('domesticSummary').hidden=!state.account.connected||!domestic;
 for(const[id,key]of[['domesticCash','cashToday'],['domesticWithdrawable','withdrawableCash'],['domesticD2','cashD2'],['domesticNet','accountNetAssets']])$(id).textContent=domestic?.available&&domestic[key]!=null?`${fmt(domestic[key],0)}원`:'—';
 $('domesticNote').textContent=domestic?.available?'같은 연결계좌의 참고 조회값입니다. 위 예수금·달러 자산과 중복 합산하지 않습니다.':(domestic?.error||'원화 계좌 요약 조회 대기 중');
 $('maxGap').textContent=p.ready?`${fmt(p.maxGap,1)}%p`:'—';$('driftStatus').textContent=p.ready?p.needsRebalance?'조정 조건 충족':'허용 오차 이내':'목표 비중 미설정';$('rows').replaceChildren();
 for(const r of p.rows||[]){
  if(r.weight>0){stops.push(`${info[r.symbol].color} ${cursor}% ${cursor+r.weight}%`);cursor+=r.weight;}
  const tr=node('tr');tr.style.setProperty('--asset-color',info[r.symbol].color);const symbol=node('td');symbol.append(node('i','ticker-mark'),node('span','position-ticker',r.symbol));tr.append(symbol);
  const value=node('td','cell-main',money(r.value));value.append(node('span','cell-sub',`${fmt(r.quantity,4)}주`));tr.append(value);
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
async function poll(){if(busy)return;busy=true;try{const r=await fetch('/api/state'),data=await r.json();if(!r.ok)throw Error(data.error);state=data;$('offline').hidden=true;render();}catch(e){$('offline').hidden=false;$('offline').textContent=e.message||'데이터 서비스 연결 끊김';$('connection').textContent='연결 끊김 · 마지막 수신 값';}finally{busy=false;if(window.parent!==window)window.parent.postMessage({type:'etf-board-ready'},'*');if(state)void applyNativeProperties();}}
$('settingsButton').onclick=()=>{if(!state)return;const f=$('settingsForm');dialogRevision=state.configRevision;for(const s of portfolioSymbols){f.elements[`qty-${s}`].value=state.settings.quantities[s]||0;f.elements[`qty-${s}`].disabled=state.account.connected;f.elements[`target-${s}`].value=state.settings.targets[s]??'';}for(const key of['cash','contribution','cash_target','threshold'])f.elements[key].value=state.settings[key];f.elements.cash.disabled=state.account.connected;$('settingsHelp').textContent=state.account.connected?'시세 목록과 실제 보유 자산을 함께 표시합니다. 실제 보유 종목의 목표도 설정하세요. 원화 현금은 달러 비중 계산에서 제외됩니다.':'종목 추가·삭제는 프로젝트의 config.json에서 편집합니다. 이 화면에서 저장한 값도 같은 파일에 반영됩니다.';$('saveError').textContent='';updateSum();$('settings').showModal();};
$('closeSettings').onclick=()=>$('settings').close();
function updateSum(){const f=$('settingsForm');let sum=Number(f.elements.cash_target.value),complete=true;for(const s of portfolioSymbols){const v=f.elements[`target-${s}`]?.value;if((v==null||v==='')&&state.settings.quantities[s]>0)complete=false;sum+=Number(v||0);}$('targetSum').textContent=`종목 + 현금 목표 합계 ${fmt(sum)}%${complete&&Math.abs(sum-100)<1e-6?' · 계산 가능':' · 실제 보유 종목의 목표와 합계 100%가 필요합니다.'}`;}
$('settingsForm').addEventListener('input',updateSum);
$('settingsForm').onsubmit=async e=>{e.preventDefault();const f=e.target,data={quantities:{},targets:{},configRevision:dialogRevision};for(const s of portfolioSymbols){data.quantities[s]=Number(f.elements[`qty-${s}`].value);data.targets[s]=f.elements[`target-${s}`].value===''?null:Number(f.elements[`target-${s}`].value);}for(const key of['cash','contribution','cash_target','threshold'])data[key]=Number(f.elements[key].value);try{const r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json','X-Session-Key':state.sessionKey},body:JSON.stringify(data)});if(!r.ok)throw Error((await r.json()).error);$('settings').close();await poll();}catch(e){$('saveError').textContent=e.message||'설정을 저장하지 못했습니다.';}};
function clock(){const now=new Date();$('clock').textContent=now.toLocaleTimeString('ko-KR',{hour12:false,hour:'2-digit',minute:'2-digit'});$('today').textContent=now.toLocaleDateString('ko-KR',{month:'long',day:'numeric',weekday:'short'});}
async function applyNativeProperties(){
 if(!state||nativeBusy||nativeVersion===appliedNativeVersion)return;
 nativeBusy=true;const version=nativeVersion,properties=JSON.parse(JSON.stringify(nativeProperties));
 try{
  const fresh=await(await fetch('/api/state')).json();
  const r=await fetch('/api/preset',{method:'POST',headers:{'Content-Type':'application/json','X-Session-Key':fresh.sessionKey},body:JSON.stringify({properties,configRevision:fresh.configRevision})});
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
clock();setInterval(clock,1000);poll();setInterval(poll,3000);
