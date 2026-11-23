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
            "targets": {a["symbol"]: a["target_percent"] for a in config["assets"]},
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
            config["rebalance"].update(cash_usd=settings["cash"], contribution_usd=settings["contribution"],
                                       cash_target_percent=settings["cash_target"], drift_threshold_pp=settings["threshold"])
            validate_config(config)
            temp = self.path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", "utf-8")
            temp.replace(self.path)
            return self.load()
