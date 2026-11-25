"""Local-only wallpaper data service; Python standard library, no dependencies."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
import json
import math
import os
import subprocess
import threading
import time
import urllib.request
from urllib.parse import quote as quote_url
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from finance import indicators, validate_settings, rebalance, dip_plan, contribution_plan
from kb import Client, profile_store, KBResponseError
from configuration import ConfigStore, settings_from_config, manual_portfolios

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ.get("LOCALAPPDATA", str(ROOT))) / "ETFPersonalWallpaper"
STATE.mkdir(parents=True, exist_ok=True)
CONFIGS = ConfigStore()
LOCK = threading.RLock()
MARKET = {}
DIP_QUOTE = {}
DIP_STATE_PATH = STATE / 'dip-strategy-state.json'
try:
    DIP_RUNTIME = json.loads(DIP_STATE_PATH.read_text('utf-8'))
    if not isinstance(DIP_RUNTIME, dict):
        DIP_RUNTIME = {}
except (OSError, ValueError):
    DIP_RUNTIME = {}
FX = {'pair': 'USD/KRW', 'rate': None, 'source': 'Yahoo Finance · 참고 환율'}
ACCOUNT = None
ACCOUNT_ERROR = None
KB = Client()
STOP = threading.Event()
ASSET_NAMES = {"/": ("index.html", "text/html; charset=utf-8"),
               "/index.html": ("index.html", "text/html; charset=utf-8"),
               "/app.js": ("app.js", "text/javascript; charset=utf-8"),
               "/styles.css": ("styles.css", "text/css; charset=utf-8")}
SESSION_KEY = os.urandom(24).hex()
LAST_EDITOR_OPEN = 0


def open_config_editor():
    """Open only the live config in a native editor; no caller-supplied path or command."""
    global LAST_EDITOR_OPEN
    with LOCK:
        if time.monotonic() - LAST_EDITOR_OPEN < 3:
            return
        editor = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'notepad.exe'
        subprocess.Popen([str(editor), str(CONFIGS.path.resolve())], shell=False)
        LAST_EDITOR_OPEN = time.monotonic()


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=18) as resp:
        return json.load(resp)


def market_quote(symbol, history_range="1y", rsi_period=14):
    data = fetch_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_url(symbol, safe='')}?range={history_range}&interval=1d&events=div%2Csplits")
    result = data["chart"]["result"][0]
    meta = result["meta"]
    if meta.get("currency") != "USD":
        raise ValueError("USD 시세가 아닌 종목입니다.")
    adj = result["indicators"].get("adjclose", [{}])[0].get("adjclose")
    if not adj:
        raise ValueError("배당·분할 조정 가격 없음")
    # Do not mix a partial session into the daily RSI and drawdown history.
    # The regular-session end supplied by the feed handles DST and half days.
    session = meta.get("currentTradingPeriod", {}).get("regular", {})
    today_start, today_end = session.get("start", 0), session.get("end", 0)
    pairs = [(ts, float(p)) for ts, p in zip(result["timestamp"], adj)
             if p is not None and p > 0 and not (today_start <= ts <= today_end and time.time() < today_end + 120)]
    prices = [p for _, p in pairs]
    if len(prices) <= rsi_period:
        raise ValueError("일별 가격 이력 부족")
    stats = indicators(prices, rsi_period)
    raw = result["indicators"]["quote"][0]["close"]
    raw_pairs = [(ts, p) for ts, p in zip(result["timestamp"], raw) if p is not None]
    regular_ts = meta.get("regularMarketTime", 0)
    previous = None
    # Compare the quote to the preceding trading day's unadjusted close.
    try:
        tz = ZoneInfo("America/New_York")
    except ZoneInfoNotFoundError:
        # Feed gmtoffset is sufficient for adjacent sessions except a DST weekend;
        # timestamps around the open remain on the same date in either offset.
        from datetime import timedelta
        tz = timezone(timedelta(seconds=meta.get("gmtoffset", -18000)))
    quote_day = datetime.fromtimestamp(regular_ts, tz).date()
    for ts, price in reversed(raw_pairs):
        if datetime.fromtimestamp(ts, tz).date() < quote_day:
            previous = price
            break
    price = float(meta["regularMarketPrice"])
    if not math.isfinite(price) or price <= 0:
        raise ValueError("유효한 현재가가 없습니다.")
    return {"symbol": symbol, "price": price, "change": (price / previous - 1) * 100 if previous else None,
            "quoteAt": regular_ts, "fetchedAt": time.time(), "indicatorAt": pairs[-1][0],
            "historyStart": pairs[0][0], "history": prices, "source": "Yahoo Finance (비공식)",
            "completedCloses": [(ts, float(p)) for ts, p in raw_pairs if p > 0 and not (today_start <= ts <= today_end and time.time() < today_end + 120)],
            "error": None, "historyRange": history_range, "rsiPeriod": rsi_period, **stats}


def refresh_dip(symbol):
    global DIP_QUOTE
    try:
        value = market_quote(symbol, '2y', 14)
        with LOCK:
            DIP_QUOTE = value
    except Exception:
        with LOCK:
            DIP_QUOTE = {**DIP_QUOTE, 'error': '신호 일봉 조회 실패'}


def current_dip_plan(config):
    global DIP_RUNTIME
    quote = DIP_QUOTE if DIP_QUOTE.get('symbol') == config['signal_symbol'] else {}
    runtime, plan = dip_plan(config, quote, DIP_RUNTIME)
    if runtime != DIP_RUNTIME:
        temp = DIP_STATE_PATH.with_suffix('.tmp')
        temp.write_text(json.dumps(runtime), 'utf-8')
        os.replace(temp, DIP_STATE_PATH)
        DIP_RUNTIME = runtime
    return plan


def refresh_one(symbol, history_range="1y", rsi_period=14):
    try:
        quote = market_quote(symbol, history_range, rsi_period)
        with LOCK:
            MARKET[symbol] = quote
    except Exception as exc:
        with LOCK:
            old = dict(MARKET.get(symbol, {"symbol": symbol}))
            old["error"] = f"시세 조회 실패 ({type(exc).__name__})"
            MARKET[symbol] = old


def refresh_fx():
    global FX
    try:
        data = fetch_json('https://query1.finance.yahoo.com/v8/finance/chart/KRW%3DX?range=1d&interval=1d')
        meta = data['chart']['result'][0]['meta']
        rate = float(meta['regularMarketPrice'])
        if meta.get('currency') != 'KRW' or not math.isfinite(rate) or rate <= 0:
            raise ValueError('Invalid USD/KRW rate')
        with LOCK:
            FX = {'pair': 'USD/KRW', 'rate': rate, 'quoteAt': meta.get('regularMarketTime'),
                  'fetchedAt': time.time(), 'source': 'Yahoo Finance · 참고 환율', 'error': None}
    except Exception:
        with LOCK:
            FX = {**FX, 'error': '환율 갱신 실패 · 이전 조회값'}


def refresh_loop():
    global ACCOUNT, ACCOUNT_ERROR
    last_run, last_key = 0, None
    while not STOP.is_set():
        try:
            config, _, error = CONFIGS.load()
        except ValueError:
            STOP.wait(1)
            continue
        symbols = tuple(a["symbol"] for a in config["assets"])
        ind = config["indicators"]
        account_config = config['account']
        store = profile_store(account_config['profile'])
        if not account_config['enabled']:
            with LOCK:
                ACCOUNT, ACCOUNT_ERROR = None, None
        dip = config['dip_strategy']
        key = (symbols, ind["history_range"], ind["rsi_period"], dip['enabled'], dip['signal_symbol'], account_config['enabled'], account_config['profile'], store.stat().st_mtime_ns if store.exists() else 0)
        with LOCK:
            retry_needed = any(not MARKET.get(s) or MARKET[s].get('error') for s in symbols) or FX.get('error') or (dip['enabled'] and (not DIP_QUOTE or DIP_QUOTE.get('error')))
        interval = min(15, config['refresh_seconds']) if retry_needed else config['refresh_seconds']
        if key == last_key and time.time() - last_run < interval:
            STOP.wait(1)
            continue
        with ThreadPoolExecutor(max_workers=4) as pool:
            fx_task = pool.submit(refresh_fx)
            dip_task = pool.submit(refresh_dip, dip['signal_symbol']) if dip['enabled'] else None
            list(pool.map(lambda s: refresh_one(s, ind["history_range"], ind["rsi_period"]), symbols))
            fx_task.result()
            if dip_task:
                dip_task.result()
        if account_config['enabled'] and store.exists():
            try:
                snapshot = KB.snapshot(symbols, account_config['profile'])
                with LOCK:
                    ACCOUNT, ACCOUNT_ERROR = snapshot, None
            except Exception as exc:
                with LOCK:
                    ACCOUNT_ERROR = exc.public_message if isinstance(exc, KBResponseError) else f"KB 연결 확인 필요 ({type(exc).__name__})"
        last_run, last_key = time.time(), key
        STOP.wait(1)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_bytes(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def json_reply(self, status, data):
        self.send_bytes(status, json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8")

    def allowed_host(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def do_GET(self):
        if not self.allowed_host():
            return self.json_reply(403, {"error": "Local host only"})
        path = self.path.split("?")[0]
        if path == '/ready.js':
            return self.send_bytes(200, b'/* Local Companion ready. No account data. */', 'text/javascript; charset=utf-8')
        if path == "/api/state":
            try:
                with LOCK:
                    config, revision, config_error = CONFIGS.load()
                    symbols = tuple(a["symbol"] for a in config["assets"])
                    settings = settings_from_config(config)
                    quotes = {s: dict(MARKET[s]) for s in symbols if s in MARKET}
                    for q in quotes.values():
                        if q.get("historyRange") != config["indicators"]["history_range"] or q.get("rsiPeriod") != config["indicators"]["rsi_period"]:
                            q.update(rsi=None, mdd=None, drawdown=None, history=[], error="지표 설정 변경 · 새 이력 조회 중")
                    account = {"connected": False, "label": "수동 입력 · KB 계좌 미연결"}
                    account_config = config['account']
                    enabled = account_config['enabled']
                    configured = profile_store(account_config['profile']).exists()
                    account.update(enabled=enabled, configured=configured, profile=account_config['profile'])
                    if enabled and not configured:
                        account['label'] = f"KB 키 미등록 · {account_config['profile']}"
                    elif not enabled:
                        account['label'] = '수동 입력 · KB 연결 꺼짐'
                    elif enabled:
                        account['label'] = f"KB 연결 대기 · {account_config['profile']}"
                    account_matches = enabled and ACCOUNT and ACCOUNT.get('profile', 'default') == account_config['profile'] and all(s in ACCOUNT["quantities"] for s in symbols)
                    if account_matches:
                        settings["quantities"] = dict(ACCOUNT['quantities'])
                        for s in settings['quantities']:
                            settings['targets'].setdefault(s, None)
                        settings["cash"] = ACCOUNT["cash"]
                        account.update(connected=True, label=f"KB 자동 조회 · {account_config['profile']}", syncedAt=ACCOUNT['syncedAt'], excluded=ACCOUNT['excluded'])
                        account.update(positions=ACCOUNT.get('positions', []), cashByCurrency=ACCOUNT.get('cash_by_currency', {}), domestic=ACCOUNT.get('domestic', {}))
                    if enabled and configured and ACCOUNT_ERROR:
                        account["label"] = ACCOUNT_ERROR + (" · 이전 잔고" if ACCOUNT else " · 수동 입력 사용 중")
                        account["error"] = True
                    portfolio_symbols = tuple(dict.fromkeys([*symbols, *settings['quantities']]))
                    if account_matches:
                        portfolio_symbols = tuple(dict.fromkeys([
                            *(s for s, quantity in settings['quantities'].items() if quantity > 0),
                            *(s for s in symbols if settings['targets'].get(s) is not None)
                        ]))
                    portfolio_quotes = dict(quotes)
                    if account_matches:
                        for position in ACCOUNT.get('positions', []):
                            if position['symbol'] not in portfolio_quotes and position['price'] > 0:
                                portfolio_quotes[position['symbol']] = {'price': position['price']}
                    portfolio = rebalance(settings, portfolio_quotes, portfolio_symbols)
                    if account_matches and ACCOUNT.get('excluded'):
                        portfolio['ready'] = False
                        portfolio['reason'] = 'USD 이외의 보유 종목이 있어 전체 계좌 리밸런싱을 보류합니다.'
                        for row in portfolio.get('rows', []):
                            row['delta'] = row['shares'] = None
                    if enabled and ACCOUNT and (not account_matches or ACCOUNT_ERROR or time.time() - ACCOUNT["syncedAt"] > max(300, config["refresh_seconds"] * 2.5)):
                        portfolio["ready"] = False
                        portfolio["reason"] = "KB 잔고가 최신인지 확인할 수 없습니다. 연결 복구 후 리밸런싱을 계산합니다."
                        for row in portfolio.get("rows", []):
                            row["delta"] = row["shares"] = None
                    dip_result = current_dip_plan(config['dip_strategy'])
                    contribution_quotes = {**MARKET, **portfolio_quotes}
                    blocked = None
                    if enabled and (not account_matches or ACCOUNT_ERROR or time.time()-ACCOUNT['syncedAt']>max(300,config['refresh_seconds']*2.5)):
                        blocked = 'KB 최신 잔고 확인 후 추가 매수를 계산합니다.'
                    contribution_result = contribution_plan(config['contribution_plan'], settings['quantities'], contribution_quotes, FX, blocked, settings['cash'] if account_matches else None)
                    if config['contribution_plan']['enabled']:
                        portfolio['ready'] = False
                        portfolio['reason'] = '신규 입금 배분은 상단 QQQ·QLD 계획 참고 · 아래는 전체 계좌 보유내역'
                        for row in portfolio.get('rows', []):
                            row['delta'] = row['shares'] = None
                return self.json_reply(200, {"quotes": quotes, "settings": settings, "dipPlan": dip_result,
                    "contributionPlan": contribution_result,
                    "portfolio": portfolio, "fx": dict(FX), "manualAccounts": manual_portfolios(config['manual_accounts']), "sessionKey": SESSION_KEY,
                    "account": account, "config": config, "configRevision": revision,
                    "configError": config_error, "now": time.time()})
            except (ValueError, OSError) as exc:
                return self.json_reply(503, {"error": str(exc)})
        if path == "/api/config":
            try:
                return self.json_reply(200, CONFIGS.load()[0])
            except ValueError as exc:
                return self.json_reply(503, {"error": str(exc)})
        if path.startswith("/fonts/"):
            filename = path.removeprefix("/fonts/")
            if filename in ("geist-latin.woff2", "geist-mono-latin.woff2"):
                return self.send_bytes(200, (ROOT / "web" / "fonts" / filename).read_bytes(), "font/woff2")
        if path in ASSET_NAMES:
            filename, mime = ASSET_NAMES[path]
            return self.send_bytes(200, (ROOT / "web" / filename).read_bytes(), mime)
        return self.json_reply(404, {"error": "Not found"})

    def do_POST(self):
        if not self.allowed_host() or self.headers.get("X-Session-Key") != SESSION_KEY:
            return self.json_reply(403, {"error": "요청을 다시 시도하세요."})
        if self.path not in ("/api/settings", "/api/preset", "/api/manual-accounts", "/api/edit-config"):
            return self.json_reply(404, {"error": "Not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 49152:
                raise ValueError("설정 크기 오류")
            payload = json.loads(self.rfile.read(length))
            if self.path == '/api/edit-config':
                open_config_editor()
                return self.json_reply(200, {'ok': True})
            if self.path == '/api/manual-accounts':
                CONFIGS.save_manual_accounts(payload.get('manual_accounts'), payload.get('configRevision'))
                return self.json_reply(200, {'ok': True})
            if self.path == '/api/preset':
                props = payload.get('properties', {})
                if not isinstance(props, dict) or set(props) - {'portfoliojson', 'kbconnect', 'kbprofile'}:
                    raise ValueError('지원하지 않는 월페이퍼 속성입니다.')
                CONFIGS.apply_wallpaper_properties(props, payload.get('configRevision'))
                return self.json_reply(200, {'ok': True})
            config = CONFIGS.load()[0]
            symbols = tuple(a["symbol"] for a in config["assets"])
            symbols = tuple(dict.fromkeys([*symbols, *(ACCOUNT['quantities'] if config['account']['enabled'] and ACCOUNT else [])]))
            data = validate_settings(payload, symbols)
            if config['account']['enabled'] and ACCOUNT:
                # Broker quantities are read-only; retain manual fallback values on disk.
                data["quantities"] = {a["symbol"]: a["quantity"] for a in config["assets"]}
                data["cash"] = config["rebalance"]["cash_usd"]
            CONFIGS.save_settings(data, payload.get("configRevision"))
            self.json_reply(200, {"ok": True})
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            self.json_reply(400, {"error": str(exc) or "설정 값을 확인하세요."})
        except OSError:
            self.json_reply(503, {'error': 'JSON 편집기를 열지 못했습니다. Edit Config.cmd를 실행하세요.'})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    threading.Thread(target=refresh_loop, daemon=True).start()
    print(f"ETF wallpaper: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STOP.set()
        server.server_close()


if __name__ == "__main__":
    main()
