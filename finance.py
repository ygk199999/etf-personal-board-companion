"""Price indicators and configurable allocation. All money is USD."""
import math

SYMBOLS = ("QQQ", "SPY", "TLT", "SCHD")


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
