"""환율 수집.

환율 소스는 갱신 주기와 기준이 제각각이라 여러 곳을 순서대로 시도합니다.

  1) 한국수출입은행 - 매매기준율. 영업일 11시경 하루 한 번 고시. 인증키 필요
  2) 우리은행      - 고시 화면을 읽습니다. 하루에도 여러 회차로 바뀌어 자주 갱신됩니다
  3) 네이버 금융    - 실시간에 가깝고 전일 대비도 주지만 비공식 경로라 언제든 막힐 수 있음
  4) Frankfurter    - 유럽중앙은행 고시. 평일 하루 한 번(16:00 CET). 전일 대비 계산 가능
  5) ExchangeRate-API - 하루 한 번. 앞이 모두 안 될 때의 마지막 보루

2번은 공개 API 가 아니라 화면을 읽는 방식이라 페이지가 바뀌면 깨질 수 있습니다.
그래서 태그 구조에 기대지 않고 표 머리글에서 '매매기준율' 칸을 찾아 읽고,
값이 상식 범위를 벗어나면 버립니다. 실패하면 조용히 다음 소스로 넘어갑니다.

어느 소스에서 몇 시 기준으로 받은 값인지 항상 함께 돌려주므로, 화면에서 "실시간"인지
"일 고시"인지 구분해 보여 줄 수 있습니다.
"""

from __future__ import annotations

import html
import json
import re
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
    {"code": "CADKRW", "label": "캐나다달러/원", "base": "CAD", "quote": "KRW", "unit": 1, "digits": 2},
    {"code": "CHFKRW", "label": "스위스프랑/원", "base": "CHF", "quote": "KRW", "unit": 1, "digits": 2},
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
    "CADKRW": "FX_CADKRW",
    "CHFKRW": "FX_CHFKRW",
    "USDJPY": "FX_USDJPY",
    "EURUSD": "FX_EURUSD",
}


TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


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


class WooriBankRates:
    """우리은행 환율조회 페이지에서 매매기준율을 읽어 온다.

    공개 API 가 아니라 화면을 읽는 방식이라, 페이지가 바뀌면 깨질 수 있습니다.
    그래서 특정 태그·클래스에 기대지 않고 **표 머리글에서 '매매기준율' 칸을 찾아**
    그 열의 숫자를 가져옵니다. 실패하면 조용히 다음 소스로 넘어갑니다.

    수출입은행이 영업일 11시경 하루 한 번 고시하는 것과 달리, 은행 고시환율은
    하루에도 여러 차례(회차별) 바뀝니다. 그래서 10분마다 확인하는 것이 실제로
    의미가 있습니다.
    """

    name = "woori"
    label = "우리은행 매매기준율"
    realtime = False       # 실시간 체결가는 아니고 은행 고시환율이다

    URL = "https://spot.wooribank.com/pot/Dream?withyou=FXXRT0011"
    HEADERS = {
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ko-KR,ko;q=0.9",
    }

    ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
    CELL_RE = re.compile(r"<(t[dh])([^>]*)>(.*?)</\1\s*>", re.S | re.I)
    COLSPAN_RE = re.compile(r"colspan\s*=\s*[\"']?(\d+)", re.I)
    STAMP_RE = re.compile(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})[^0-9]{0,12}(\d{1,2}):(\d{2})")
    ROUND_RE = re.compile(r"(\d+)\s*회\s*차")

    # 표에 통화가 어떻게 적혀 있든 알아보도록 코드와 한글 이름을 모두 둔다.
    CURRENCIES = {
        "USD": ("USD", ("미국",)),
        "JPY": ("JPY", ("일본",)),
        "EUR": ("EUR", ("유로", "유럽")),
        "CNY": ("CNY", ("중국", "위안")),
        "CNH": ("CNY", ()),
        "GBP": ("GBP", ("영국",)),
        "AUD": ("AUD", ("호주",)),
        "CAD": ("CAD", ("캐나다",)),
        "CHF": ("CHF", ("스위스",)),
    }
    # 파싱이 엉뚱하게 됐는지 걸러내는 상식 범위(1단위당 원화)
    SANE_RANGE = {
        "USD": (500, 3000), "JPY": (5, 30), "EUR": (600, 3500),
        "CNY": (80, 400), "GBP": (700, 4000), "AUD": (400, 2000),
        "CAD": (400, 2000), "CHF": (600, 3000),
    }

    def __init__(self, http_get):
        self.http_get = http_get

    @staticmethod
    def _decode(body: bytes) -> str:
        """한국 은행 페이지는 UTF-8 일 수도, EUC-KR(CP949) 일 수도 있다."""
        head = body[:2048].lower()
        order = ["utf-8", "cp949"]
        if b"euc-kr" in head or b"ks_c_5601" in head:
            order = ["cp949", "utf-8"]
        for encoding in order:
            try:
                return body.decode(encoding)
            except UnicodeDecodeError:
                continue
        return body.decode("utf-8", "replace")

    @classmethod
    def _cells(cls, row_html: str) -> list[str]:
        """행의 칸들을 뽑되, colspan 은 그만큼 자리를 차지하도록 펼친다.

        우리은행 표는 머리글이 2단입니다. 1행에 '송금'(colspan=2), '현찰'(colspan=4),
        '매매기준율'(rowspan=2) 이 나오므로, colspan 을 펼쳐야 머리글의 칸 위치가
        데이터 행의 칸 위치와 맞습니다. 펼치지 않으면 '매매기준율' 을 5번째로 보고
        엉뚱하게 현찰 값을 집습니다.
        """
        out = []
        for _tag, attrs, inner in cls.CELL_RE.findall(row_html):
            text = WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", inner))).strip()
            span = cls.COLSPAN_RE.search(attrs or "")
            width = max(1, min(int(span.group(1)), 20)) if span else 1
            out.extend([text] * width)
        return out

    @classmethod
    def _currency_of(cls, cells: list[str]) -> tuple[str, int] | None:
        """행의 앞쪽 칸에서 통화를 알아내고, 엔화처럼 100단위면 그 단위도 돌려준다."""
        head = " ".join(cells[:2]).upper()
        for token, (code, names) in cls.CURRENCIES.items():
            if re.search(rf"\b{token}\b", head) or any(name in head for name in names):
                # '일본 100엔' 처럼 단위가 적혀 있으면 그대로 쓴다. 한글이 붙어 있어
                # 단어 경계(\b)로는 못 잡으므로 그냥 포함 여부로 본다.
                unit = 100 if "100" in head else (100 if code == "JPY" else 1)
                return code, unit
        return None

    @classmethod
    def parse(cls, text: str) -> tuple[dict[str, float], str]:
        """페이지 HTML 에서 '1단위당 원화' 표와 기준 시각을 뽑는다."""
        krw: dict[str, float] = {}
        column: int | None = None

        for row_html in cls.ROW_RE.findall(text):
            cells = cls._cells(row_html)
            if not cells:
                continue

            # 머리글에서 매매기준율 칸의 위치를 기억한다.
            for index, cell in enumerate(cells):
                if "매매" in cell and "기준" in cell:
                    column = index
                    break
            else:
                found = cls._currency_of(cells)
                if not found:
                    continue
                code, unit = found
                # '1.750%' 같은 스프레드율 칸은 환율이 아니므로 후보에서 뺀다.
                numbers = [(i, _to_float(c)) for i, c in enumerate(cells) if "%" not in c]
                numbers = [(i, v) for i, v in numbers if v]
                if not numbers:
                    continue
                # 머리글을 찾았으면 그 칸을, 못 찾았으면 첫 숫자를 쓴다.
                value = next((v for i, v in numbers if i == column), None) if column is not None else None
                if value is None:
                    value = numbers[0][1]
                per_unit = value / unit
                low, high = cls.SANE_RANGE.get(code, (0, float("inf")))
                if low <= per_unit <= high:
                    krw.setdefault(code, per_unit)

        stamp = ""
        match = cls.STAMP_RE.search(TAG_RE.sub(" ", text))
        if match:
            y, mo, d, h, mi = match.groups()
            stamp = f"{y}-{int(mo):02d}-{int(d):02d} {int(h):02d}:{mi}"
        turn = cls.ROUND_RE.search(TAG_RE.sub(" ", text))
        if turn:
            stamp = f"{stamp} {turn.group(1)}회차".strip()
        return krw, stamp

    def fetch(self, pairs: list[dict]) -> dict:
        body = self.http_get(self.URL, headers=self.HEADERS, timeout=15.0)
        krw, stamp = self.parse(self._decode(body))
        if "USD" not in krw:
            raise ValueError("페이지에서 매매기준율을 찾지 못했습니다(화면 구조가 바뀌었을 수 있습니다).")

        krw["KRW"] = 1.0
        usd_krw = krw["USD"]
        table = {code: usd_krw / value for code, value in krw.items() if value}
        table["USD"] = 1.0

        out = {}
        for pair in pairs:
            value = _cross(table, pair["base"], pair["quote"], pair["unit"])
            if value is not None:
                out[pair["code"]] = {"value": value, "change": None, "asOf": stamp or "우리은행 고시"}
        return out


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

    # --rate-source 로 고를 수 있는 이름
    SOURCE_NAMES = ("exim", "woori", "naver", "frankfurter", "open-er-api")

    def __init__(self, http_get, interval: float = 600.0, on_update=None,
                 enabled: bool = True, exim_key: str = "", prefer: str = "auto"):
        super().__init__(name="rates")
        self.http_get = http_get
        self.interval = max(20.0, interval)
        self.on_update = on_update
        self.enabled = enabled
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        available = {
            "woori": WooriBankRates(http_get),
            "naver": NaverFinanceRates(http_get),
            "frankfurter": FrankfurterRates(http_get),
            "open-er-api": OpenErApiRates(http_get),
        }
        # 수출입은행은 인증키가 있을 때만 쓸 수 있다.
        if exim_key:
            available["exim"] = KoreaEximRates(http_get, exim_key)

        if prefer and prefer != "auto":
            chosen = available.get(prefer)
            if chosen is None:
                print(f"[warn] 환율 소스 '{prefer}' 를 쓸 수 없어 자동 선택으로 돌립니다.", file=sys.stderr)
            else:
                # 지정한 소스를 맨 앞에 두되, 그것이 실패하면 나머지로 내려간다.
                self.sources = [chosen] + [s for k, s in available.items() if k != prefer]
                self._init_snapshot()
                return

        # 기본 순서: 수출입은행(키 있을 때) → 우리은행 → 네이버 금융 → ECB → 폴백
        order = ["exim", "woori", "naver", "frankfurter", "open-er-api"]
        self.sources = [available[k] for k in order if k in available]
        self._init_snapshot()

    def _init_snapshot(self) -> None:
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
