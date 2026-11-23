"""Read-only KB Securities connector, based on the official B2C JSON examples.

https://openapi.kbsec.com/api/kbs/guide/json/b2c
https://github.com/kbsecurities/kb-openapi/tree/main/example/python
"""
import base64
import ctypes
from ctypes import wintypes
import getpass
import argparse
import json
import os
from pathlib import Path
import time
import urllib.request

from finance import SYMBOLS, number
from configuration import validate_profile

BASE = "https://developer.kbsec.com:32484"
STORE = Path(os.environ.get("LOCALAPPDATA", ".")) / "ETFPersonalWallpaper" / "kb-credentials.dpapi"


def profile_store(profile='default'):
    profile = validate_profile(profile)
    return STORE if profile == 'default' else STORE.parent / 'profiles' / (profile + '.dpapi')


def save_credentials(profile, key, secret):
    if not key.strip() or not secret.strip():
        raise ValueError('두 값을 모두 입력해야 합니다.')
    store = profile_store(profile)
    store.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps({'appKey': key.strip(), 'appSecret': secret.strip()}).encode('utf-8')
    temp = store.with_suffix('.tmp')
    temp.write_bytes(base64.b64encode(protect(raw)))
    temp.replace(store)


def setup_gui(profile='default'):
    import tkinter as tk
    from tkinter import ttk, messagebox
    validate_profile(profile)
    window = tk.Tk()
    window.title('KB Open API 등록 · ' + profile)
    window.geometry('570x310')
    window.resizable(False, False)
    frame = ttk.Frame(window, padding=24)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='KB 계좌 연결 · 로컬 프로필 ' + profile, font=('맑은 고딕', 14, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='키는 이 PC에 암호화해 저장하며 Steam 프리셋에 넣지 않습니다.').pack(anchor='w', pady=(8, 18))
    ttk.Label(frame, text='appKey').pack(anchor='w')
    key = ttk.Entry(frame, show='*', width=68)
    key.pack(fill='x', pady=(4, 12))
    ttk.Label(frame, text='appSecret').pack(anchor='w')
    secret = ttk.Entry(frame, show='*', width=68)
    secret.pack(fill='x', pady=(4, 18))
    def save_input():
        try:
            save_credentials(profile, key.get(), secret.get())
        except Exception:
            messagebox.showerror('저장 실패', '키 두 값을 확인해 다시 입력하세요.', parent=window)
            return
        key.delete(0, 'end')
        secret.delete(0, 'end')
        messagebox.showinfo('암호화 저장 완료', '서버가 이 프로필로 계좌 조회를 시도합니다.\n월페이퍼의 연결 상태를 확인하세요.', parent=window)
        window.destroy()
    ttk.Button(frame, text='이 PC에 암호화 저장', command=save_input).pack(fill='x')
    key.focus_set()
    window.attributes('-topmost', True)
    window.after(1500, lambda: window.attributes('-topmost', False))
    window.mainloop()


class Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def protect(raw, decrypt=False):
    """Windows DPAPI, current Windows user; no cleartext credential file."""
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI가 필요합니다.")
    buf = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel.LocalFree(output.pbData)


def setup(profile='default'):
    store = profile_store(profile)
    print("KB Open API 키 입력 · 입력값은 화면에 표시되지 않습니다.")
    key = getpass.getpass("appKey: ").strip()
    secret = getpass.getpass("appSecret: ").strip()
    if not key or not secret:
        raise ValueError("두 값을 모두 입력해야 합니다.")
    store.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps({"appKey": key, "appSecret": secret}).encode("utf-8")
    temp = store.with_suffix(".tmp")
    temp.write_bytes(base64.b64encode(protect(raw)))
    temp.replace(store)
    print("현재 Windows 계정으로 암호화해 저장했습니다. 실행 중인 서버는 다음 조회 때 연결을 시도합니다.")


def checked_body(response):
    header = response.get("dataHeader", {})
    if str(header.get("resultCode")) != "200" or header.get("processFlag") != "A":
        # Never include the response, token, account, or server message in UI/logs.
        raise ValueError("KB 응답 오류 또는 조회 권한 부족")
    body = response.get("dataBody")
    if not isinstance(body, dict):
        raise ValueError("KB 응답 형식 확인 필요")
    return body


def parse_account(balance, cash, symbols=SYMBOLS):
    b = checked_body(balance)
    c = checked_body(cash)
    if str(b.get("nxt_key", "")).strip():
        raise ValueError("KB 잔고가 여러 페이지입니다. 연속 조회 검증 전에는 계산하지 않습니다.")
    if not isinstance(b.get("Record2"), list) or not isinstance(c.get("Record1"), list):
        raise ValueError("KB 잔고 필드 검증 필요")
    quantities = {s: 0.0 for s in symbols}
    excluded = 0
    for row in b["Record2"]:
        symbol = str(row.get("is_cd", "")).strip().upper()
        if not symbol:
            continue
        if symbol not in symbols:
            excluded += 1
            continue
        if str(row.get("crncy_clsf_nm", "")).strip() != "USD":
            raise ValueError("보유 ETF 통화가 USD가 아닙니다.")
        quantities[symbol] += number(str(row["frgn_hld_q_p6"]).strip())
    usd = [row for row in c["Record1"] if str(row.get("crncy_cd", "")).strip() == "USD"]
    if len(usd) != 1:
        raise ValueError("KB 달러 예수금 확인 필요")
    amount = number(str(usd[0]["fcrncy_tfnd"]).strip())
    return {"quantities": quantities, "cash": amount, "excluded": excluded, "syncedAt": time.time()}


class Client:
    def __init__(self):
        self.token = None
        self.expires = 0
        self.fingerprint = None

    def post(self, path, body, headers=None):
        if path not in ("/oauth2/token", "/api/v1/spqm2226", "/api/v1/skqm3350"):
            raise ValueError("허용되지 않은 API")
        payload = {"dataHeader": {"ipAddr": "", "macAddr": ""}, "dataBody": body}
        request = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)

    def snapshot(self, symbols=SYMBOLS, profile='default'):
        store = profile_store(profile)
        fingerprint = (str(store), store.stat().st_mtime_ns)
        keys = json.loads(protect(base64.b64decode(store.read_bytes()), decrypt=True))
        if fingerprint != self.fingerprint or time.time() >= self.expires:
            auth = checked_body(self.post("/oauth2/token", {**keys, "grantType": "client_credentials"}))
            self.token = auth["access_token"]
            self.expires = time.time() + max(0, int(auth.get("expires_in", 3600)) - 120)
            self.fingerprint = fingerprint
        headers = {"appKey": keys["appKey"], "Authorization": f"bearer {self.token}"}
        try:
            balance = self.post("/api/v1/spqm2226", {"std_crncy_f": "1", "exch_r_aplc_f": "1", "fee_clsf": "0", "cn_f": "", "nxt_key": "", "mktpr_aplc_clsf": ""}, headers)
            cash = self.post("/api/v1/skqm3350", {}, headers)
            result = parse_account(balance, cash, symbols)
            result['profile'] = profile
            return result
        except Exception:
            self.expires = 0
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', default='default')
    parser.add_argument('--gui', action='store_true')
    args = parser.parse_args()
    try:
        setup_gui(args.profile) if args.gui else setup(args.profile)
    except Exception as exc:
        print(f"설정을 완료하지 못했습니다 ({type(exc).__name__}). 키를 확인해 다시 실행하세요.")
        raise SystemExit(1)
