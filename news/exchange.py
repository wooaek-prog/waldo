"""환율 수집.

환율 소스는 갱신 주기와 기준이 제각각이라 여러 곳을 순서대로 시도합니다.

  1) 한국수출입은행 - 매매기준율. 영업일 11시경 하루 한 번 고시. 인증키 필요
  2) 네이버 금융    - 실시간에 가깝고 전일 대비도 주지만 비공식 경로라 언제든 막힐 수 있음
  3) Frankfurter    - 유럽중앙은행 고시. 평일 하루 한 번(16:00 CET). 전일 대비 계산 가능
  4) ExchangeRate-API - 하루 한 번. 앞이 모두 안 될 때의 마지막 보루

어느 소스에서 몇 시 기준으로 받은 값인지 항상 함께 돌려주므로, 화면에서 "실시간"인지
"일 고시"인지 구분해 보여 줄 수 있습니다.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# code, 화면 표기, 기준통화, 상대통화, 표시 단위(엔은 관례상 100엔 기준)
PAIRS: list[dict] = [
    {"code": "USDKRW", "label": "달러/원", "base": "USD", "quote": "KRW", "unit": 1, "digits": 2},
    {"code": "JPYKRW", "label": "엔/원", "base": "JPY", "quote": "KRW", "unit": 100, "digits": 2},
    {"code": "EURKRW", "label": "유로/원", "base": "EUR", "quote": "KRW", "unit": 1, "digits": 2},
    {"code": "CNYKRW", "label": "위안/원", "base": "CNY", "quote": "KRW", "unit": 1, "digits": 2},
    {"code": "GBPKRW", "label": "파운드/원", "base": "GBP", "quote": "KRW", "unit": 1, "digits": 2},
    {"code": "AUDKRW", "label": "호주달러/원", "base": "AUD", "quote": "KRW", "unit": 1, "digits": 2},
    {"code": "USDJPY", "label": "달러/엔", "base": "USD", "quote": "JPY", "unit": 1, "digits": 2},
    {"code": "EURUSD", "label": "유로/달러", "base": "EUR", "quote": "USD", "unit": 1, "digits": 4},
]

PAIR_BY_CODE = {p["code"]: p for p in PAIRS}
# 화면에서 기본으로 켜 두는 통화쌍
DEFAULT_CODES = ["USDKRW", "JPYKRW", "EURKRW", "USDJPY"]

FRANKFURTER = "https://api.frankfurter.dev/v1"
OPEN_ER_API = "https://open.er-api.com/v6/latest/USD"
NAVER_MARKETINDEX = "https://m.stock.naver.com/front-api/marketIndex/prices"

# 네이버 금융의 통화쌍 코드
NAVER_CODES = {
    "USDKRW": "FX_USDKRW",
    "JPYKRW": "FX_JPYKRW",
    "EURKRW": "FX_EURKRW",
    "CNYKRW": "FX_CNYKRW",
    "GBPKRW": "FX_GBPKRW",
    "AUDKRW": "FX_AUDKRW",
    "USDJPY": "FX_USDJPY",
    "EURUSD": "FX_EURUSD",
}


def _cross(table: dict[str, float], base: str, quote: str, unit: int) -> float | None:
    """USD 기준 환율표에서 임의의 통화쌍을 계산한다."""
    b, q = table.get(base), table.get(quote)
    if not b or not q:
        return None
    return q / b * unit


def _to_float(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


class KoreaEximRates:
    """한국수출입은행 현재환율 API. 매매기준율(deal_bas_r)을 씁니다.

    은행이 **영업일 오전 11시경에 하루 한 번 고시**하는 값입니다. 자주 조회해도
    고시 시각 전까지는 같은 값이 돌아옵니다. 그래서 조회 주기와 별개로 화면에는
    '고시일자'를 함께 보여 줍니다.

    주말·공휴일이나 11시 이전에는 빈 배열이 오므로, 값이 나올 때까지 하루씩
    거슬러 올라가며 가장 최근 고시분을 찾습니다.
    """

    name = "koreaexim"
    label = "수출입은행 매매기준율"
    realtime = False

    # 2026-04-30 부터 oapi 도메인으로 옮겨졌습니다. 구 주소도 남겨 두고 순서대로 시도합니다.
    HOSTS = (
        "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON",
        "https://www.koreaexim.go.kr/site/program/financial/exchangeJSON",
    )
    # 응답의 cur_unit → 우리 통화 코드와 표기 단위
    # cur_unit 은 대문자로 비교합니다. 위안화는 CNH 로 옵니다.
    UNITS = {
        "USD": ("USD", 1),
        "JPY(100)": ("JPY", 100),
        "EUR": ("EUR", 1),
        "CNH": ("CNY", 1),
        "CNY": ("CNY", 1),
        "GBP": ("GBP", 1),
        "AUD": ("AUD", 1),
    }
    RESULT_MESSAGES = {
        2: "DATA 코드 오류입니다.",
        3: "인증키가 올바르지 않거나 만료되었습니다. 수출입은행에서 다시 발급받아 주세요.",
        4: "오늘 조회 한도를 모두 썼습니다. 내일 다시 시도됩니다.",
    }
    LOOKBACK_DAYS = 10          # 연휴가 길어도 최근 고시분을 찾도록
    MAX_CALLS_PER_FETCH = 4     # 한 번의 갱신에서 이 이상은 호출하지 않는다

    def __init__(self, http_get, auth_key: str):
        self.http_get = http_get
        self.auth_key = auth_key
        self.host = self.HOSTS[0]
        # 같은 고시일자를 반복해서 다시 받지 않도록 기억해 둔다.
        self._cache: dict[str, dict[str, float] | None] = {}

    def _request(self, day: date) -> list:
        query = "?" + urllib.parse.urlencode({
            "authkey": self.auth_key,
            "searchdate": day.strftime("%Y%m%d"),
            "data": "AP01",
        })
        last_error = None
        # 첫 호출에서 통하는 주소를 찾으면 그다음부터는 그것만 쓴다.
        hosts = [self.host] + [h for h in self.HOSTS if h != self.host]
        for host in hosts:
            try:
                body = self.http_get(host + query, timeout=12.0)
            except Exception as exc:  # noqa: BLE001 - 다음 주소로 넘어간다
                last_error = exc
                continue
            self.host = host
            payload = json.loads(body.decode("utf-8-sig"))
            return payload if isinstance(payload, list) else []
        raise last_error or OSError("수출입은행 API 에 연결하지 못했습니다.")

    @staticmethod
    def _normalize_row(row: dict) -> dict:
        """응답 키를 소문자로 맞춘다.

        수출입은행 문서는 RESULT / CUR_UNIT / DEAL_BAS_R 처럼 대문자로 적혀 있고
        실제 응답은 소문자로 오기도 합니다. 어느 쪽이든 읽히도록 맞춰 둡니다.
        """
        return {str(key).strip().lower(): value for key, value in row.items()}

    def _table_for(self, day: date, today: date) -> dict[str, float] | None:
        """그 날짜의 고시가 있으면 '통화 1단위당 원화' 표를 돌려준다."""
        key = day.isoformat()
        if key in self._cache:
            return self._cache[key]

        rows = self._request(day)
        if not rows:
            # 주말·공휴일이거나 아직 고시 전. 지난 날짜는 앞으로도 안 바뀌니 기억해 두고,
            # 오늘치는 11시 고시를 기다려야 하므로 기억하지 않는다.
            if day < today:
                self._cache[key] = None
            return None

        krw: dict[str, float] = {"KRW": 1.0}
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            row = self._normalize_row(raw)
            result = _to_float(row.get("result"))
            if result is not None and int(result) != 1:
                code = int(result)
                raise ValueError(self.RESULT_MESSAGES.get(code, f"수출입은행 응답 코드 {code}"))
            mapped = self.UNITS.get(str(row.get("cur_unit", "")).strip().upper())
            if not mapped:
                continue
            code, unit = mapped
            value = _to_float(row.get("deal_bas_r"))
            if value:
                krw[code] = value / unit      # 1단위당 원화로 환산

        if "USD" not in krw:
            return None
        self._cache[key] = krw
        return krw

    @staticmethod
    def _as_usd_table(krw: dict[str, float]) -> dict[str, float]:
        """'1단위당 원화' 표를 USD 기준 표로 바꿔 교차계산에 태운다."""
        usd_krw = krw["USD"]
        table = {code: usd_krw / value for code, value in krw.items() if value}
        table["USD"] = 1.0
        return table

    def fetch(self, pairs: list[dict]) -> dict:
        today = datetime.now(KST).date()
        calls = 0
        latest = latest_day = None
        for back in range(self.LOOKBACK_DAYS):
            day = today - timedelta(days=back)
            if day.isoformat() not in self._cache:
                if calls >= self.MAX_CALLS_PER_FETCH:
                    break
                calls += 1
            table = self._table_for(day, today)
            if table:
                latest, latest_day = table, day
                break
        if latest is None:
            raise ValueError("최근 고시 환율을 찾지 못했습니다.")

        previous = None
        for back in range(1, self.LOOKBACK_DAYS):
            day = latest_day - timedelta(days=back)
            if day.isoformat() not in self._cache:
                if calls >= self.MAX_CALLS_PER_FETCH:
                    break
                calls += 1
            found = self._table_for(day, today)
            if found:
                previous = found
                break

        now_table = self._as_usd_table(latest)
        prev_table = self._as_usd_table(previous) if previous else None
        as_of = f"{latest_day.isoformat()} 고시"

        out = {}
        for pair in pairs:
            value = _cross(now_table, pair["base"], pair["quote"], pair["unit"])
            if value is None:
                continue
            before = _cross(prev_table, pair["base"], pair["quote"], pair["unit"]) if prev_table else None
            out[pair["code"]] = {
                "value": value,
                "change": (value - before) if before else None,
                "asOf": as_of,
            }
        return out


class FrankfurterRates:
    """유럽중앙은행 고시(평일 하루 1회). 전일 대비까지 계산해 준다."""

    name = "frankfurter"
    label = "ECB 고시(일 1회)"
    realtime = False

    def __init__(self, http_get):
        self.http_get = http_get

    def _table(self, path: str, symbols: list[str]) -> tuple[dict[str, float], str]:
        url = f"{FRANKFURTER}/{path}?base=USD&symbols={','.join(symbols)}"
        payload = json.loads(self.http_get(url, timeout=12.0).decode("utf-8"))
        table = {k: float(v) for k, v in payload.get("rates", {}).items()}
        table["USD"] = 1.0
        return table, payload.get("date", "")

    def fetch(self, pairs: list[dict]) -> dict:
        symbols = sorted({c for p in pairs for c in (p["base"], p["quote"])} - {"USD"})
        latest, as_of = self._table("latest", symbols)

        previous: dict[str, float] = {}
        try:
            day = date.fromisoformat(as_of) - timedelta(days=1)
            previous, _ = self._table(day.isoformat(), symbols)
        except (ValueError, OSError, json.JSONDecodeError):
            previous = {}  # 전일 대비는 없어도 현재값은 보여 준다

        out = {}
        for pair in pairs:
            value = _cross(latest, pair["base"], pair["quote"], pair["unit"])
            if value is None:
                continue
            before = _cross(previous, pair["base"], pair["quote"], pair["unit"]) if previous else None
            out[pair["code"]] = {
                "value": value,
                "change": (value - before) if before else None,
                "asOf": as_of,
            }
        return out


class OpenErApiRates:
    """마지막 보루. 하루 1회 갱신이고 전일 대비는 제공하지 않는다."""

    name = "open-er-api"
    label = "ExchangeRate-API(일 1회)"
    realtime = False

    def __init__(self, http_get):
        self.http_get = http_get

    def fetch(self, pairs: list[dict]) -> dict:
        payload = json.loads(self.http_get(OPEN_ER_API, timeout=12.0).decode("utf-8"))
        table = {k: float(v) for k, v in payload.get("rates", {}).items()}
        stamp = payload.get("time_last_update_unix")
        as_of = (
            datetime.fromtimestamp(stamp, KST).strftime("%Y-%m-%d %H:%M")
            if isinstance(stamp, (int, float)) else ""
        )
        out = {}
        for pair in pairs:
            value = _cross(table, pair["base"], pair["quote"], pair["unit"])
            if value is not None:
                out[pair["code"]] = {"value": value, "change": None, "asOf": as_of}
        return out


class NaverFinanceRates:
    """네이버 금융 시장지표. 실시간에 가깝고 전일 대비도 주지만 비공식 경로다.

    응답 구조가 바뀌면 조용히 실패하고 다음 소스로 넘어갑니다. 필드 이름을
    단정하지 않고 눈에 띄는 후보를 훑어 찾습니다.
    """

    name = "naver"
    label = "네이버 금융(실시간)"
    realtime = True

    VALUE_KEYS = ("closePrice", "currentPrice", "price", "basePrice", "dealBasR")
    CHANGE_KEYS = ("compareToPreviousClosePrice", "compareToPreviousPrice", "change", "changeValue")

    def __init__(self, http_get):
        self.http_get = http_get

    def _pick(self, node: dict, keys) -> float | None:
        for key in keys:
            got = _to_float(node.get(key))
            if got is not None:
                return got
        return None

    def fetch(self, pairs: list[dict]) -> dict:
        out = {}
        for pair in pairs:
            code = NAVER_CODES.get(pair["code"])
            if not code:
                continue
            url = f"{NAVER_MARKETINDEX}?category=exchange&reutersCode={code}&page=1&pageSize=1"
            payload = json.loads(self.http_get(url, timeout=10.0).decode("utf-8"))
            node = payload[0] if isinstance(payload, list) and payload else payload
            if isinstance(node, dict) and isinstance(node.get("result"), (list, dict)):
                result = node["result"]
                node = result[0] if isinstance(result, list) and result else result
            if not isinstance(node, dict):
                raise ValueError("예상과 다른 응답 구조")

            value = self._pick(node, self.VALUE_KEYS)
            if value is None:
                raise ValueError("환율 값을 찾지 못함")
            direction = str(node.get("fluctuationsType") or node.get("compareToPreviousPriceType") or "")
            change = self._pick(node, self.CHANGE_KEYS)
            if change is not None and direction in {"5", "DOWN", "down", "하락"}:
                change = -abs(change)
            out[pair["code"]] = {
                "value": value,
                "change": change,
                "asOf": str(node.get("localTradedAt") or node.get("tradeDate") or ""),
            }
        if not out:
            raise ValueError("가져온 통화쌍이 없음")
        return out


class RateService(threading.Thread):
    """환율을 주기적으로 갱신하고 최신 스냅샷을 들고 있는다."""

    daemon = True

    def __init__(self, http_get, interval: float = 600.0, on_update=None,
                 enabled: bool = True, exim_key: str = ""):
        super().__init__(name="rates")
        self.http_get = http_get
        self.interval = max(20.0, interval)
        self.on_update = on_update
        self.enabled = enabled
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.sources = []
        # 수출입은행 키가 있으면 이게 1순위. 은행 고시 매매기준율이라 기준이 분명합니다.
        if exim_key:
            self.sources.append(KoreaEximRates(http_get, exim_key))
        self.sources += [
            NaverFinanceRates(http_get),
            FrankfurterRates(http_get),
            OpenErApiRates(http_get),
        ]
        self.snapshot: dict = {
            "pairs": [{k: p[k] for k in ("code", "label", "digits")} for p in PAIRS],
            "defaultCodes": DEFAULT_CODES,
            "rates": {},
            "source": None,
            "sourceLabel": None,
            "realtime": False,
            "updatedAt": None,
            "error": None,
        }

    def get(self) -> dict:
        with self.lock:
            return dict(self.snapshot)

    def refresh_once(self) -> bool:
        errors = []
        for source in self.sources:
            try:
                rates = source.fetch(PAIRS)
            except Exception as exc:  # noqa: BLE001 - 어떤 실패든 다음 소스로 넘어간다
                detail = str(exc).strip() or type(exc).__name__
                errors.append(f"{source.name}: {detail[:120]}")
                continue
            if not rates:
                errors.append(f"{source.name}: 빈 응답")
                continue
            with self.lock:
                self.snapshot.update(
                    rates=rates,
                    source=source.name,
                    sourceLabel=source.label,
                    realtime=source.realtime,
                    updatedAt=time.time(),
                    error=None,
                )
            return True

        message = "환율을 가져오지 못했습니다 (" + ", ".join(errors) + ")"
        with self.lock:
            first = self.snapshot.get("error") != message
            self.snapshot["error"] = message
        if first:
            print(f"[warn] {message}", file=sys.stderr)
        return False

    def run(self) -> None:
        if not self.enabled:
            return
        failures = 0
        while not self.stop_event.is_set():
            ok = self.refresh_once()
            if self.on_update:
                try:
                    self.on_update(self.get())
                except Exception:  # noqa: BLE001 - 구독자 문제로 폴러가 멈추면 안 된다
                    pass

            if ok:
                failures = 0
                delay = self.interval
            else:
                # 실패하면 30초부터 조금씩 늘려 가며 다시 시도한다. 처음부터 길게
                # 쉬면 시작 직후 한 번 실패했을 때 한참 동안 빈 화면이 된다.
                failures += 1
                delay = min(30.0 * (2 ** min(failures - 1, 5)), self.interval * 3)
            self.stop_event.wait(delay)
