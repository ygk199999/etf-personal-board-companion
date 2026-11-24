"""Human-editable config.json is the sole source for watchlists and allocation."""
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import threading
from datetime import date

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"
RANGES = ("6mo", "1y", "2y", "5y", "10y", "max")


def validate_profile(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', value):
        raise ValueError('KB 프로필은 영문·숫자·밑줄·하이픈 1~32자로 입력하세요.')
    return value


def bounded(value, low, high, name, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name}: {low}~{high} 범위의 숫자가 필요합니다.")
    if integer and value != int(value):
        raise ValueError(f"{name}: 정수가 필요합니다.")
    return int(value) if integer else value


def validate_manual_accounts(accounts):
    if not isinstance(accounts, list) or len(accounts) > 2:
        raise ValueError('manual_accounts: ISA·IRP 계좌를 최대 2개 입력하세요.')
    accounts = copy.deepcopy(accounts)
    seen = set()
    for account in accounts:
        if not isinstance(account, dict) or account.get('id') not in ('isa', 'irp') or account['id'] in seen:
            raise ValueError('수기 계좌 id는 중복 없이 isa 또는 irp여야 합니다.')
        seen.add(account['id'])
        account.setdefault('name', account['id'].upper())
        if not isinstance(account['name'], str) or not 1 <= len(account['name'].strip()) <= 80:
            raise ValueError('수기 계좌 이름은 1~80자로 입력하세요.')
        if account.setdefault('currency', 'KRW') != 'KRW':
            raise ValueError('ISA·IRP 수기 입력은 원화(KRW) 기준입니다.')
        stamp = account.setdefault('as_of', '')
        if not isinstance(stamp, str):
            raise ValueError('as_of는 YYYY-MM-DD 날짜로 입력하세요.')
        if stamp:
            try:
                if date.fromisoformat(stamp).isoformat() != stamp:
                    raise ValueError()
            except ValueError:
                raise ValueError('as_of는 YYYY-MM-DD 날짜로 입력하세요.') from None
        for key, default, limit in [('cash', None, 1e15), ('contribution', 0, 1e15),
                                    ('cash_target_percent', None, 100), ('drift_threshold_pp', 5, 100)]:
            value = account.setdefault(key, default)
            if value is None and key in ('cash', 'cash_target_percent'):
                continue
            bounded(value, 0, limit, '수기 계좌 ' + key)
        holdings = account.setdefault('holdings', [])
        if not isinstance(holdings, list) or len(holdings) > 60:
            raise ValueError('수기 계좌 holdings는 최대 60개 종목의 배열입니다.')
        codes = set()
        for holding in holdings:
            if not isinstance(holding, dict):
                raise ValueError('수기 보유 종목은 JSON 객체로 입력하세요.')
            symbol = holding.get('symbol')
            if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z0-9][A-Z0-9.\-]{0,29}', symbol) or symbol in codes:
                raise ValueError('수기 종목코드는 중복 없이 영문 대문자·숫자로 입력하세요.')
            codes.add(symbol)
            name = holding.setdefault('name', symbol)
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
                raise ValueError('수기 종목 이름은 1~100자로 입력하세요.')
            bounded(holding.get('value'), 0, 1e15, '종목 평가금액 value')
            for key, limit in [('quantity', 1e15), ('target_percent', 100)]:
                value = holding.setdefault(key, None)
                if value is not None:
                    bounded(value, 0, limit, '수기 종목 ' + key)
            if holding['quantity'] == 0 and holding['value'] > 0:
                raise ValueError('평가금액이 있는 종목의 수량은 양수 또는 null로 입력하세요.')
    return accounts


def validate_dip_strategy(value):
    if not isinstance(value, dict):
        raise ValueError('dip_strategy는 JSON 객체여야 합니다.')
    value = copy.deepcopy(value)
    mode = value.setdefault('allocation_mode', 'remaining_percent')
    if mode not in ('remaining_percent', 'equal_parts'):
        raise ValueError('allocation_mode는 remaining_percent 또는 equal_parts입니다.')
    name = value.setdefault('strategy_name', '하락장 분할 매수')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError('strategy_name은 1~80자입니다.')
    if not isinstance(value.setdefault('enabled', False), bool):
        raise ValueError('dip_strategy.enabled는 true 또는 false입니다.')
    symbol = value.setdefault('signal_symbol', 'QQQM')
    if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z0-9][A-Z0-9.\-]{0,29}', symbol):
        raise ValueError('하락장 신호 종목코드를 확인하세요.')
    value['lookback_sessions'] = bounded(value.get('lookback_sessions', 252), 20, 500, 'lookback_sessions', True)
    value['starting_cash_krw'] = bounded(value.get('starting_cash_krw', 0), 0, 1e15, 'starting_cash_krw')
    value['cash_adjustment_krw'] = bounded(value.get('cash_adjustment_krw', 0), -1e15, 1e15, 'cash_adjustment_krw')
    value['spend_remaining_percent'] = bounded(value.get('spend_remaining_percent', 30), .01, 100, 'spend_remaining_percent')
    levels = value.setdefault('levels_percent', [10, 15, 20, 30])
    if not isinstance(levels, list) or not 1 <= len(levels) <= 12:
        raise ValueError('levels_percent는 1~12개 낙폭의 배열입니다.')
    for level in levels:
        bounded(level, .01, 99, '하락장 매수 낙폭')
    if levels != sorted(set(levels)):
        raise ValueError('매수 낙폭은 중복 없이 오름차순으로 입력하세요.')
    fills = value.setdefault('executions', [])
    if not isinstance(fills, list) or len(fills) > 1000:
        raise ValueError('executions는 최대 1000건의 체결 기록 배열입니다.')
    seen = set()
    for fill in fills:
        if not isinstance(fill, dict):
            raise ValueError('체결 기록은 JSON 객체여야 합니다.')
        bounded(fill.get('cycle'), 1, 1e6, '체결 cycle', True)
        level = fill.get('level_percent')
        bounded(level, .01, 99, '체결 level_percent')
        if (fill['cycle'], level) in seen:
            raise ValueError('체결 낙폭을 확인하세요. 같은 회차·단계는 한 건으로 합산하세요.')
        seen.add((fill['cycle'], level))
        bounded(fill.get('amount_krw'), .01, 1e15, '실제 체결 amount_krw')
        stamp = fill.get('date')
        try:
            if not isinstance(stamp, str) or date.fromisoformat(stamp).isoformat() != stamp:
                raise ValueError()
        except ValueError:
            raise ValueError('체결 date는 YYYY-MM-DD로 입력하세요.') from None
    balance = value['starting_cash_krw'] + value['cash_adjustment_krw'] - sum(f['amount_krw'] for f in fills)
    if balance < 0:
        raise ValueError('하락장 대기자금보다 체결 기록 합계가 큽니다.')
    return value


def manual_portfolios(accounts):
    """Calculate each KRW account from entered valuations; never mix with live USD assets."""
    results = []
    for account in accounts:
        cash = account['cash']
        total = sum(h['value'] for h in account['holdings']) + (cash or 0)
        targets = [h['target_percent'] for h in account['holdings']] + [account['cash_target_percent']]
        valid = all(v is not None for v in targets) and abs(sum(v or 0 for v in targets) - 100) < 1e-6
        ready = valid and cash is not None and total + account['contribution'] > 0
        gaps = [abs((h['value'] / total * 100 if total else 0) - (h['target_percent'] or 0)) for h in account['holdings']]
        cash_gap = abs(((cash or 0) / total * 100 if total else 0) - (account['cash_target_percent'] or 0))
        max_gap = max([cash_gap, *gaps]) if valid else None
        needs = ready and (max_gap >= account['drift_threshold_pp'] or account['contribution'] > 0 or total == 0)
        rows = []
        for h in account['holdings']:
            delta = ((total + account['contribution']) * h['target_percent'] / 100 - h['value'] if needs else 0) if ready else None
            shares = delta * h['quantity'] / h['value'] if delta is not None and h['value'] > 0 and h['quantity'] else None
            rows.append({**h, 'weight': h['value'] / total * 100 if total and cash is not None else None,
                         'delta': delta, 'shares': shares})
        results.append({'id': account['id'], 'name': account['name'], 'asOf': account['as_of'],
                        'currency': 'KRW', 'source': 'manual', 'cash': cash,
                        'total': total if cash is not None else None, 'rows': rows,
                        'ready': ready, 'needsRebalance': needs, 'maxGap': max_gap,
                        'reason': '' if ready else '현금(없으면 0), 보유 평가금액과 종목·현금 목표 합계 100%를 입력하세요.'})
    return results


def validate_config(data):
    if not isinstance(data, dict):
        raise ValueError("설정 최상위는 JSON 객체여야 합니다.")
    data = copy.deepcopy(data)
    title = data.get("title", "내 투자 대시보드")
    if not isinstance(title, str) or not title.strip() or len(title) > 80:
        raise ValueError("title: 1~80자 제목이 필요합니다.")
    if data.get("currency", "USD") != "USD":
        raise ValueError("currency: 현재 USD 종목·달러 현금만 지원합니다.")
    data["title"], data["currency"] = title, "USD"
    account = data.setdefault('account', {'enabled': True, 'profile': 'default'})
    if not isinstance(account, dict) or not isinstance(account.get('enabled', True), bool):
        raise ValueError('account.enabled는 true 또는 false여야 합니다.')
    account.setdefault('enabled', True)
    account['profile'] = validate_profile(account.get('profile', 'default'))
    data["refresh_seconds"] = bounded(data.get("refresh_seconds", 120), 60, 3600, "refresh_seconds", True)
    ind = data.setdefault("indicators", {})
    if not isinstance(ind, dict):
        raise ValueError("indicators는 객체여야 합니다.")
    ind["rsi_period"] = bounded(ind.get("rsi_period", 14), 2, 100, "rsi_period", True)
    ind.setdefault("history_range", "1y")
    if ind["history_range"] not in RANGES:
        raise ValueError("history_range: 6mo, 1y, 2y, 5y, 10y, max 중 선택하세요.")
    reb = data.setdefault("rebalance", {})
    if not isinstance(reb, dict):
        raise ValueError("rebalance는 객체여야 합니다.")
    for key, default, upper in [("cash_target_percent", 0, 100), ("drift_threshold_pp", 5, 100), ("cash_usd", 0, 1e15), ("contribution_usd", 0, 1e15)]:
        reb[key] = bounded(reb.get(key, default), 0, upper, key)
    extra_targets = reb.setdefault('position_targets', {})
    if not isinstance(extra_targets, dict):
        raise ValueError('position_targets는 종목별 목표 비중 객체여야 합니다.')
    for symbol, target in extra_targets.items():
        if not re.fullmatch(r'[A-Z0-9^][A-Z0-9.^=\-]{0,29}', symbol):
            raise ValueError('position_targets 티커 형식을 확인하세요.')
        if target is not None:
            bounded(target, 0, 100, symbol + '.target_percent')
    assets = data.get("assets")
    if not isinstance(assets, list) or not 1 <= len(assets) <= 24:
        raise ValueError("assets: 1~24개 종목을 입력하세요.")
    seen = set()
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            raise ValueError(f"assets[{index}]는 객체여야 합니다.")
        symbol = asset.get("symbol")
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=\-]{0,29}", symbol):
            raise ValueError(f"assets[{index}].symbol: 대문자 Yahoo 티커를 입력하세요.")
        if symbol in seen:
            raise ValueError(f"중복 티커: {symbol}")
        seen.add(symbol)
        for key in ("name", "category"):
            asset.setdefault(key, symbol if key == "name" else "USD 자산")
            if not isinstance(asset[key], str) or len(asset[key]) > 100:
                raise ValueError(f"{symbol}.{key}: 100자 이하 텍스트가 필요합니다.")
        asset.setdefault("color", "#c9a45a")
        if not isinstance(asset["color"], str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", asset["color"]):
            raise ValueError(f"{symbol}.color: #RRGGBB 형식이 필요합니다.")
        asset["quantity"] = bounded(asset.get("quantity", 0), 0, 1e15, f"{symbol}.quantity")
        target = asset.setdefault("target_percent", None)
        if target is not None:
            bounded(target, 0, 100, f"{symbol}.target_percent")
    data['manual_accounts'] = validate_manual_accounts(data.get('manual_accounts', [
        {'id': 'isa', 'name': 'KB ISA'}, {'id': 'irp', 'name': 'KB IRP'}]))
    data['dip_strategy'] = validate_dip_strategy(data.get('dip_strategy', {}))
    return data


def settings_from_config(config):
    return {"quantities": {a["symbol"]: a["quantity"] for a in config["assets"]},
            "targets": {**config['rebalance'].get('position_targets', {}), **{a["symbol"]: a["target_percent"] for a in config["assets"]}},
            "cash": config["rebalance"]["cash_usd"], "contribution": config["rebalance"]["contribution_usd"],
            "cash_target": config["rebalance"]["cash_target_percent"], "threshold": config["rebalance"]["drift_threshold_pp"]}


class ConfigStore:
    def __init__(self, path=CONFIG):
        self.path, self.lock = Path(path), threading.RLock()
        if self.path == CONFIG and not self.path.exists():
            example = ROOT / "config.example.json"
            if example.exists():
                try:
                    with self.path.open("x", encoding="utf-8") as output:
                        output.write(example.read_text("utf-8"))
                except FileExistsError:
                    pass
        self.good = None
        self.revision = ""
        self.error = None

    def load(self):
        with self.lock:
            try:
                raw = self.path.read_bytes()
                revision = hashlib.sha256(raw).hexdigest()
                if revision != self.revision:
                    data = validate_config(json.loads(raw.decode("utf-8-sig")))
                    self.good, self.revision = data, revision
                self.error = None
            except (ValueError, OSError, TypeError) as exc:
                self.error = f"config.json 오류: {exc}"
                if self.good is None:
                    raise ValueError(self.error) from None
            return copy.deepcopy(self.good), self.revision, self.error

    def save_manual_accounts(self, accounts, revision):
        with self.lock:
            config, current, error = self.load()
            if error or current != revision:
                raise ValueError('설정이 변경됐거나 오류가 있습니다. 편집창을 닫고 다시 열어주세요.')
            config['manual_accounts'] = validate_manual_accounts(accounts)
            temp = self.path.with_suffix('.json.tmp')
            temp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', 'utf-8')
            temp.replace(self.path)
            return self.load()

    def save_settings(self, settings, revision):
        with self.lock:
            config, current, error = self.load()
            if error:
                raise ValueError("config.json 오류를 먼저 수정하세요. 이전 설정으로 덮어쓰지 않습니다.")
            if current != revision:
                raise ValueError("설정 파일이 변경됐습니다. 창을 닫고 다시 열어주세요.")
            for a in config["assets"]:
                s = a["symbol"]
                a["quantity"], a["target_percent"] = settings["quantities"][s], settings["targets"][s]
            configured_symbols = {a['symbol'] for a in config['assets']}
            config['rebalance']['position_targets'] = {s: value for s, value in settings['targets'].items() if s not in configured_symbols}
            config["rebalance"].update(cash_usd=settings["cash"], contribution_usd=settings["contribution"],
                                       cash_target_percent=settings["cash_target"], drift_threshold_pp=settings["threshold"])
            validate_config(config)
            temp = self.path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", "utf-8")
            temp.replace(self.path)
            return self.load()

    def apply_wallpaper_properties(self, properties, revision):
        """Apply public WE preset settings; credentials never enter presets."""
        with self.lock:
            config, current, error = self.load()
            if error:
                raise ValueError('config.json 오류를 먼저 수정하세요.')
            if revision != current:
                raise ValueError('설정 파일이 변경됐습니다. 프리셋을 다시 적용하세요.')
            original = copy.deepcopy(config)
            def value(name, default=None):
                entry = properties.get(name, default)
                return entry.get('value', default) if isinstance(entry, dict) else entry
            raw = value('portfoliojson', '')
            if not isinstance(raw, str) or len(raw) > 32768:
                raise ValueError('설정 JSON은 32768자 이하 문자열이어야 합니다.')
            if raw.strip():
                preset = json.loads(raw)
                if not isinstance(preset, dict):
                    raise ValueError('설정 JSON은 객체여야 합니다.')
                def no_secrets(item):
                    if isinstance(item, dict):
                        for key, child in item.items():
                            if re.sub(r'[^a-z]', '', key.lower()) in ('appkey', 'appsecret', 'apikey', 'accesstoken', 'password'):
                                raise ValueError('API 키는 프리셋에 저장하지 마세요. 로컬 KB 프로필을 사용하세요.')
                            no_secrets(child)
                    elif isinstance(item, list):
                        for child in item: no_secrets(child)
                no_secrets(preset)
                allowed = {'title', 'currency', 'refresh_seconds', 'indicators', 'rebalance', 'assets', 'account', 'manual_accounts', 'dip_strategy'}
                if set(preset) - allowed:
                    raise ValueError('설정 JSON에 지원하지 않는 최상위 항목이 있습니다.')
                for key, item in preset.items():
                    if key in ('indicators', 'rebalance', 'account', 'dip_strategy') and isinstance(item, dict):
                        config.setdefault(key, {}).update(item)
                    elif key == 'assets' and isinstance(item, list):
                        previous = {a['symbol']: a for a in config['assets']}
                        config['assets'] = [{**previous.get(a.get('symbol'), {}), **a} if isinstance(a, dict) else a for a in item]
                    else:
                        config[key] = item
            if 'kbconnect' in properties:
                enabled = value('kbconnect')
                if not isinstance(enabled, bool):
                    raise ValueError('KB 연결 속성은 true 또는 false여야 합니다.')
                config['account']['enabled'] = enabled
            if 'kbprofile' in properties:
                config['account']['profile'] = validate_profile(value('kbprofile'))
            config = validate_config(config)
            if config != original:
                temp = self.path.with_suffix('.json.tmp')
                temp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', 'utf-8')
                temp.replace(self.path)
            return self.load()
