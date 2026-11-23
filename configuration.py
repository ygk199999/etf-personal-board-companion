"""Human-editable config.json is the sole source for watchlists and allocation."""
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import threading

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
                allowed = {'title', 'currency', 'refresh_seconds', 'indicators', 'rebalance', 'assets', 'account'}
                if set(preset) - allowed:
                    raise ValueError('설정 JSON에 지원하지 않는 최상위 항목이 있습니다.')
                for key, item in preset.items():
                    if key in ('indicators', 'rebalance', 'account') and isinstance(item, dict):
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
