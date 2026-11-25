"""Price indicators and configurable allocation. All money is USD."""
import math

SYMBOLS = ("QQQ", "SPY", "TLT", "SCHD")


def contribution_plan(config, positions, quotes, fx, blocked=None, usd_cash=None):
    """Allocate only new money toward two target buckets, never propose sales."""
    result = {'enabled': config['enabled'], 'ready': False, 'rows': []}
    if not config['enabled']:
        return result
    if blocked:
        return {**result, 'message': blocked}
    core_symbols = config['core_symbols']
    leveraged = config['leveraged_symbol']
    values = [0., 0.]
    for symbol, quantity in positions.items():
        if not quantity or symbol not in [*core_symbols, leveraged]:
            continue
        q = quotes.get(symbol, {})
        if q.get('error') or not q.get('price'):
            return {**result, 'message': f'{symbol} 보유 평가 시세 확인 필요'}
        values[1 if symbol == leveraged else 0] += quantity*q['price']
    source=config.get('funding_source','manual')
    if source=='usd_cash' and usd_cash is None:
        return {**result,'message':'KB 달러 예수금 조회 후 배분합니다.'}
    entered_amount = usd_cash if source=='usd_cash' else config['amount']
    currency = 'USD' if source=='usd_cash' else config['currency']
    amount = entered_amount
    rate = fx.get('rate')
    if currency == 'KRW' and amount:
        if not rate or fx.get('error') or __import__('time').time()-fx.get('fetchedAt',0)>300:
            return {**result, 'message': '추가 원화 투입금 환산을 위한 최신 환율 대기'}
        amount /= rate
    total = sum(values)
    target = config['core_target_percent']/100
    core_buy = min(amount, max(0., (total+amount)*target-values[0]))
    buys = [core_buy, amount-core_buy]
    rows=[]
    for i, symbol in enumerate([config['core_buy_symbol'], leveraged]):
        q=quotes.get(symbol,{})
        if q.get('error') or not q.get('price'):
            return {**result, 'message': f'{symbol} 매수 수량 계산 시세 대기'}
        if q.get('fetchedAt') and __import__('time').time()-q['fetchedAt']>300:
            return {**result, 'message': f'{symbol} 최신 시세 갱신 대기'}
        rows.append({'symbol': symbol, 'value': values[i], 'weight': values[i]/total*100 if total else None,
                     'target': (target if i==0 else 1-target)*100, 'buyUsd': buys[i],
                     'buyKrw': buys[i]*rate if rate else None, 'shares': buys[i]/q['price'],
                     'afterWeight': (values[i]+buys[i])/(total+amount)*100 if total+amount else None})
    exact = total+amount>0 and abs((values[0]+core_buy)/(total+amount)-target)<1e-8
    message = '추가 투입금 입력 필요 · 보유 비중을 반영해 매수만 계산' if not amount else '추가 매수 후 목표 비중 도달' if exact else '이번 투입금으로 목표 비중 도달 불가 · 부족한 쪽에 우선 매수'
    if source=='usd_cash' and not amount:
        message='KB 달러 예수금 대기 · 입금·환전 반영 후 자동 배분'
    return {**result, 'ready': True, 'rows': rows, 'message': message, 'amount': entered_amount,
            'currency': currency, 'fundingSource':source, 'amountUsd': amount, 'exact': exact,
            'coreSymbols': core_symbols, 'excludedSymbols': [s for s,v in positions.items() if v and s not in [*core_symbols, leveraged]]}


def dip_plan(config, quote, runtime):
    """Read-only purchase guidance. Only recorded actual fills consume the reserve."""
    import copy
    import time
    state = copy.deepcopy(runtime)
    equal_parts = config.get('allocation_mode') == 'equal_parts'
    balance = config['starting_cash_krw'] + config['cash_adjustment_krw'] - sum(f['amount_krw'] for f in config['executions'])
    base = {'enabled': config['enabled'], 'cashKrw': balance, 'symbol': config['signal_symbol'],
            'spendPercent': config['spend_remaining_percent'], 'ready': False, 'rows': [],
            'strategyName': config.get('strategy_name', '하락장 분할 매수'),
            'allocationMode': config.get('allocation_mode', 'remaining_percent'), 'parts': len(config['levels_percent'])}
    base['signalOnly'] = config.get('signal_only', False)
    if not config['enabled']:
        return state, {**base, 'message': '하락장 추가 매수 꺼짐'}
    bars = (quote or {}).get('completedCloses', [])
    if not bars or (quote or {}).get('error'):
        return state, {**base, 'message': '완료 일봉 조회 대기 · 매수 판단 보류'}
    last_ts = bars[-1][0]
    # Allow market holidays/weekends, but never reuse an indefinitely stale feed.
    if time.time() - quote.get('fetchedAt', 0) > 300 or time.time() - last_ts > 7*86400:
        return state, {**base, 'message': '일봉 갱신 지연 · 매수 판단 보류'}
    identity = [config['signal_symbol'], config['lookback_sessions']]
    if equal_parts:
        identity += ['equal_parts', config['levels_percent']]
    if state.get('identity') != identity:
        history = bars[-config['lookback_sessions']:]
        peak_ts, peak = max(history, key=lambda b: b[1])
        # A changed signal starts a distinct cycle; preserve all previously spent cash.
        cycle = max([state.get('cycle', 0), *(f['cycle'] for f in config['executions'])], default=0)
        cycle = cycle + 1 if state.get('identity') else max(1, cycle)
        state = {'identity': identity, 'cycle': cycle, 'anchor': peak, 'anchorAt': peak_ts,
                 'active': False, 'processedAt': last_ts-1, 'cycleBudget': balance}
    # Rebase a stored reference if a split adjusted its historical closing price.
    adjusted_anchor = next((p for ts,p in bars if ts == state['anchorAt']), None)
    if adjusted_anchor is not None:
        state['anchor'] = adjusted_anchor
    for ts, close in bars:
        if ts <= state.get('processedAt', 0):
            continue
        if state['active'] and close >= state['anchor']:
            state['cycle'] += 1
            state['active'] = False
        if not state['active']:
            state['cycleBudget'] = balance + sum(f['amount_krw'] for f in config['executions'] if f['cycle']==state['cycle'])
            history = [b for b in bars if b[0] <= ts][-config['lookback_sessions']:]
            peak_ts, peak = max(history, key=lambda b: b[1])
            state['anchor'], state['anchorAt'] = peak, peak_ts
            if (1-close/peak)*100 >= config['levels_percent'][0]-1e-8:
                state['active'] = True
        state['processedAt'] = ts
    close = bars[-1][1]
    drawdown = max(0., (1-close/state['anchor'])*100)
    completed = {f['level_percent']: f for f in config['executions'] if f['cycle']==state['cycle']}
    if not state['active']:
        state['cycleBudget'] = balance + sum(f['amount_krw'] for f in completed.values())
    projected = balance
    rows = []
    budget = state.get('cycleBudget', balance + sum(f['amount_krw'] for f in completed.values()))
    count = len(config['levels_percent'])
    unit = math.floor(budget / count)
    for index, level in enumerate(config['levels_percent']):
        fill = completed.get(level)
        planned = (unit if index < count-1 else round(budget-unit*(count-1))) if equal_parts else round(projected*config['spend_remaining_percent']/100)
        amount = fill['amount_krw'] if fill else min(projected, planned)
        if not fill:
            projected -= amount
        reached = drawdown >= level-1e-8
        rows.append({'level': level, 'amountKrw': None if base['signalOnly'] else amount, 'status': 'completed' if fill else 'due' if reached and (base['signalOnly'] or amount>0) else 'waiting'})
    due = [r for r in rows if r['status']=='due']
    message = f"{len(due)}개 단계 조건 충족 · 실제 매수 후 체결 기록 필요" if due else '다음 낙폭 조건 대기 · 정기 적립 유지'
    if base['signalOnly']:
        message = f"{len(due)}개 낙폭 조건 충족 · 투입금은 현재 보유비중에 맞춰 배분" if due else '추가 매수 낙폭 대기 · 정기 적립 유지'
    return state, {**base, 'ready': True, 'cycle': state['cycle'], 'referenceClose': state['anchor'],
                   'referenceAt': state['anchorAt'], 'signalClose': close, 'signalAt': last_ts,
                   'drawdown': -drawdown, 'rows': rows, 'message': message}


def number(value):
    if isinstance(value, bool):
        raise ValueError("숫자를 입력하세요.")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("0 이상의 유한한 숫자를 입력하세요.")
    return result


def indicators(prices, period=14):
    if not prices or any(not math.isfinite(p) or p <= 0 for p in prices):
        raise ValueError("유효한 가격 이력이 필요합니다.")
    peak, mdd = prices[0], 0.0
    for price in prices:
        peak = max(peak, price)
        mdd = min(mdd, price / peak - 1)
    rsi = None
    if len(prices) > period:
        changes = [b - a for a, b in zip(prices, prices[1:])]
        gain = sum(max(c, 0) for c in changes[:period]) / period
        loss = sum(max(-c, 0) for c in changes[:period]) / period
        for change in changes[period:]:
            gain = (gain * (period - 1) + max(change, 0)) / period
            loss = (loss * (period - 1) + max(-change, 0)) / period
        rsi = 50.0 if gain == loss == 0 else (100.0 if loss == 0 else 100 - 100 / (1 + gain / loss))
    return {"rsi": rsi, "mdd": mdd * 100, "drawdown": (prices[-1] / peak - 1) * 100}


def validate_settings(data, symbols=SYMBOLS):
    return {
        "quantities": {s: number(data.get("quantities", {}).get(s, 0)) for s in symbols},
        "targets": {s: (None if data.get("targets", {}).get(s) in (None, "")
                        else number(data["targets"][s])) for s in symbols},
        "cash": number(data.get("cash", 0)),
        "contribution": number(data.get("contribution", 0)),
        "cash_target": number(data.get("cash_target", 0)),
        "threshold": number(data.get("threshold", 0)),
    }


def rebalance(settings, quotes, symbols=SYMBOLS):
    settings = validate_settings(settings, symbols)
    if any(not quotes.get(s) or not quotes[s].get("price") for s in symbols):
        return {"ready": False, "reason": "관심 종목의 시세가 모두 필요합니다."}
    values = {s: settings["quantities"][s] * quotes[s]["price"] for s in symbols}
    current = sum(values.values()) + settings["cash"]
    total = current + settings["contribution"]
    targets = settings["targets"]
    cash_target = settings["cash_target"]
    valid = cash_target <= 100 and all(v is not None and v <= 100 for v in targets.values()) and abs(sum(v or 0 for v in targets.values()) + cash_target - 100) < 1e-6
    max_gap = max([abs(values[s] / current * 100 - targets[s]) for s in symbols] + [abs(settings["cash"] / current * 100 - cash_target)]) if valid and current else 0
    needs = valid and total > 0 and (max_gap >= settings["threshold"] or settings["contribution"] > 0 or current == 0)
    rows = []
    for s in symbols:
        weight = values[s] / current * 100 if current else 0
        delta = (total * targets[s] / 100 - values[s] if needs else 0) if valid and total > 0 else None
        rows.append({"symbol": s, "quantity": settings["quantities"][s], "value": values[s],
                     "weight": weight, "target": targets[s],
                     "gap": weight - targets[s] if valid else None,
                     "delta": delta, "shares": delta / quotes[s]["price"] if delta is not None else None})
    return {"ready": valid and total > 0, "reason": "" if valid and total > 0 else "종목 + 현금 목표 합계 100%와 보유 자산 또는 투입 현금이 필요합니다.",
            "total": current, "afterContribution": total, "cash": settings["cash"], "rows": rows,
            "needsRebalance": needs, "maxGap": max_gap, "targetCash": total * cash_target / 100 if valid else None}
