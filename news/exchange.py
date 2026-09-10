"""환율 수집.

무료로 쓸 수 있는 환율 소스는 갱신 주기가 제각각이라 여러 곳을 순서대로 시도합니다.

  1) 네이버 금융  - 실시간에 가깝고 전일 대비도 주지만 비공식 경로라 언제든 막힐 수 있음
  2) Frankfurter - 유럽중앙은행 고시. 평일 하루 한 번(16:00 CET). 전일 대비 계산 가능
  3) ExchangeRate-API - 하루 한 번. 위 둘이 모두 안 될 때의 마지막 보루

어느 소스에서 몇 시 기준으로 받은 값인지 항상 함께 돌려주므로, 화면에서 "실시간"인지
"일 고시"인지 구분해 보여 줄 수 있습니다.
"""

from __future__ import annotations

import json
import threading
import time
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

    def __init__(self, http_get, interval: float = 60.0, on_update=None, enabled: bool = True):
        super().__init__(name="rates")
        self.http_get = http_get
        self.interval = max(20.0, interval)
        self.on_update = on_update
        self.enabled = enabled
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.sources = [
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
                errors.append(f"{source.name}: {type(exc).__name__}")
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

        with self.lock:
            self.snapshot["error"] = "환율을 가져오지 못했습니다 (" + ", ".join(errors) + ")"
        return False

    def run(self) -> None:
        if not self.enabled:
            return
        while not self.stop_event.is_set():
            changed = self.refresh_once()
            if self.on_update:
                try:
                    self.on_update(self.get())
                except Exception:  # noqa: BLE001 - 구독자 문제로 폴러가 멈추면 안 된다
                    pass
            # 실패했으면 조금 더 길게 쉬었다가 다시 시도한다
            self.stop_event.wait(self.interval if changed else self.interval * 3)
