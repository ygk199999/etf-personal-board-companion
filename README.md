# ETF Personal Board — Local Companion

Wallpaper Engine에 시세·RSI·MDD·보유 자산 데이터를 제공하는 Windows 로컬 Python 서버입니다.

## 설치

1. 이 저장소를 다운로드하고 Windows에 Python 3.13을 설치합니다.
2. `Start ETF Board.cmd`를 실행합니다. 별도 Python 패키지는 필요하지 않습니다.
3. `http://127.0.0.1:8766/`에서 미리보기를 확인합니다.
4. Steam 창작마당에서 월페이퍼를 구독합니다. 로컬 서버는 실행 중이어야 합니다.

## 설정

최초 실행 시 `config.example.json`을 복사해 `config.json`을 만듭니다.
`Edit Config.cmd`에서 종목·보유 수량·목표 비중·현금·허용 오차를 편집합니다.
설정은 자동 반영됩니다. 필드 설명은 `CONFIG_GUIDE.txt`를 참고하세요.
개인 설정 파일 `config.json`은 Git에서 제외되며 예제에는 실제 보유내역이 없습니다.

## KB 연동

KB Open API 사용 신청 후 `Setup KB API.cmd`에서 인증키를 입력합니다.
키는 Windows DPAPI로 암호화해 사용자 로컬 데이터 폴더에 저장합니다.
키와 실제 계좌 정보를 저장소나 이슈에 올리지 마세요.
주문 기능 없이 잔고 조회만 수행합니다. 실제 계좌 연동 검증은 키 발급 후 필요합니다.

## 데이터 기준

USD 종목 1~24개, Yahoo Finance 비공식 시세이며 거래소 실시간을 보장하지 않습니다.
RSI·MDD는 배당·분할 조정 완료 일봉 기준입니다.
설정한 종목과 USD 현금만 계산하며 수수료·세금·환전·미체결 주문은 미반영입니다.
서버는 127.0.0.1에만 바인딩하며 Steam 월페이퍼는 서버 화면을 표시합니다.

## 테스트

```powershell
py -3.13 -m unittest -v test_finance.py test_configuration.py
```

## 글꼴

Geist / Geist Mono: SIL Open Font License. `web/fonts/*-OFL.txt` 참고.
원작 Market Board 소스와 이미지·폰트는 이 저장소에 포함하지 않습니다.
