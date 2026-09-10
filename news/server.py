#!/usr/bin/env python3
"""CJ / HDC / 현대엘리베이터 계열사 네이버뉴스 실시간 모니터.

표준 라이브러리만 사용합니다(설치 불필요).

    python3 news/server.py

서버가 백그라운드에서 계열사 키워드를 계속 검색하고, 새 기사가 네이버뉴스에
등록되는 즉시 SSE(Server-Sent Events)로 브라우저에 밀어 넣습니다.

데이터 소스
  1) 네이버 검색 API (권장)
     - NAVER API HUB (네이버 클라우드). 2026년 이관 후 신규 발급처입니다.
     - 옛 개발자센터 키도 그대로 받습니다(이관 신청한 기존 사용자, 2027-06-30 까지).
     - 두 창구는 시작할 때 자동으로 판별합니다. --api 로 지정할 수도 있습니다.
  2) 키가 없을 때 - 구글 뉴스 RSS 한국어 폴백 (키 없이 동작, 정확도는 낮음)
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import queue
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "companies.json"
INDEX_PATH = BASE_DIR / "index.html"
STATE_PATH = BASE_DIR / ".state.json"
KEY_PATH = BASE_DIR / ".naver_key.json"  # 한 번 입력한 API 키를 이 컴퓨터에만 저장

KEY_RESET_HINT = (
    "키를 다시 넣으려면 news 폴더의 set-key-windows.bat(맥은 set-key-mac.command)를 더블클릭하거나, "
    "news 폴더의 .naver_key.json 파일을 지우고 다시 실행하세요."
)

KEY_ISSUE_HINT = (
    "네이버 클라우드 콘솔 → All Services → Application Services → NAVER API HUB → "
    "Application 선택 → API 관리의 [인증 정보] 에서 Client ID / Client Secret 을 복사하세요. "
    "'계정관리 → 인증키 관리' 의 ncp_iam_ 로 시작하는 Access Key 가 아닙니다."
)

KST = timezone(timedelta(hours=9))
# 2026년 네이버가 검색 API 를 개발자센터에서 NAVER API HUB(네이버 클라우드)로 이관했습니다.
# 신규 발급은 API HUB 뿐이고, 이관 신청을 해 둔 기존 사용자만 개발자센터 주소를
# 2027-06-30 까지 쓸 수 있습니다.
APIHUB_NEWS = "https://naverapihub.apigw.ntruss.com/search/v1/news"
DEVCENTER_NEWS = "https://openapi.naver.com/v1/search/news.json"
GOOGLE_RSS = "https://news.google.com/rss/search"
USER_AGENT = "waldo-news-monitor/1.0 (+https://github.com/wooaek-prog/waldo)"

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

# 네이버 뉴스 제휴 언론사 도메인 → 표기명(자주 나오는 것만; 없으면 호스트명 표시)
PRESS_BY_HOST = {
    "mk.co.kr": "매일경제",
    "hankyung.com": "한국경제",
    "sedaily.com": "서울경제",
    "edaily.co.kr": "이데일리",
    "fnnews.com": "파이낸셜뉴스",
    "mt.co.kr": "머니투데이",
    "asiae.co.kr": "아시아경제",
    "newsis.com": "뉴시스",
    "yna.co.kr": "연합뉴스",
    "yonhapnews.co.kr": "연합뉴스",
    "news1.kr": "뉴스1",
    "heraldcorp.com": "헤럴드경제",
    "etnews.com": "전자신문",
    "dt.co.kr": "디지털타임스",
    "chosun.com": "조선일보",
    "biz.chosun.com": "조선비즈",
    "donga.com": "동아일보",
    "joongang.co.kr": "중앙일보",
    "hani.co.kr": "한겨레",
    "khan.co.kr": "경향신문",
    "seoul.co.kr": "서울신문",
    "kmib.co.kr": "국민일보",
    "segye.com": "세계일보",
    "hankookilbo.com": "한국일보",
    "munhwa.com": "문화일보",
    "imaeil.com": "매일신문",
    "inews24.com": "아이뉴스24",
    "zdnet.co.kr": "지디넷코리아",
    "ajunews.com": "아주경제",
    "newdaily.co.kr": "뉴데일리",
    "thebell.co.kr": "더벨",
    "dealsite.co.kr": "딜사이트",
    "econovill.com": "이코노믹리뷰",
    "ebn.co.kr": "EBN",
    "cnews.co.kr": "건설경제",
    "cnews.dnews.co.kr": "대한경제",
    "dnews.co.kr": "대한경제",
    "kbs.co.kr": "KBS",
    "imnews.imbc.com": "MBC",
    "news.sbs.co.kr": "SBS",
    "ytn.co.kr": "YTN",
    "wowtv.co.kr": "한국경제TV",
    "mbn.co.kr": "MBN",
    "tvchosun.com": "TV조선",
    "jtbc.co.kr": "JTBC",
}


# --------------------------------------------------------------------------
# 유틸
# --------------------------------------------------------------------------
def clean_text(raw: str) -> str:
    """네이버 API가 돌려주는 <b> 태그와 HTML 엔티티를 제거한다."""
    return WS_RE.sub(" ", html.unescape(TAG_RE.sub("", raw or ""))).strip()


def normalize(text: str) -> str:
    """별칭 비교용 정규화: 공백/구두점 제거 + 소문자."""
    return re.sub(r"[\s\-_.·ㆍ&()\[\]'\"]+", "", (text or "")).lower()


def host_of(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def press_of(url: str) -> str:
    host = host_of(url)
    if not host:
        return ""
    if host in PRESS_BY_HOST:
        return PRESS_BY_HOST[host]
    parts = host.split(".")
    for i in range(len(parts) - 1):
        cand = ".".join(parts[i:])
        if cand in PRESS_BY_HOST:
            return PRESS_BY_HOST[cand]
    return host


def is_naver_news(url: str) -> bool:
    """네이버 뉴스(본문 페이지)에 등록된 기사인지."""
    host = host_of(url)
    return host.endswith("news.naver.com")


def canonical_link(url: str) -> str:
    """추적 파라미터를 떼어낸 중복 판정용 링크."""
    if not url:
        return ""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    keep = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query)
        if k in {"oid", "aid", "article_id", "idxno", "no", "newsid", "id"}
    ]
    return urllib.parse.urlunsplit(
        (parts.scheme, (parts.hostname or "").lower(), parts.path.rstrip("/"), urllib.parse.urlencode(keep), "")
    )


def parse_pubdate(raw: str) -> datetime:
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return datetime.now(KST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


# --------------------------------------------------------------------------
# HTTPS 인증서
# --------------------------------------------------------------------------
SSL_CONTEXT: ssl.SSLContext | None = None


def build_ssl_context(ca_bundle: str = "", quiet: bool = False) -> ssl.SSLContext:
    """HTTPS 검증에 쓸 인증서 저장소를 준비한다.

    파이썬을 python.org 설치본으로 깔면(특히 맥) 루트 인증서가 비어 있어
    CERTIFICATE_VERIFY_FAILED 가 납니다. 그럴 때 pip 로 함께 깔리는 certifi
    번들이 있으면 그걸 대신 씁니다. 검증을 끄지는 않습니다.
    """
    if ca_bundle:
        try:
            context = ssl.create_default_context(cafile=ca_bundle)
        except (OSError, ssl.SSLError) as exc:
            print(f"[warn] --ca-bundle 파일을 읽지 못했습니다: {ca_bundle}\n       {exc}", file=sys.stderr)
            print("       경로가 맞는지, 인증서 파일(.pem/.crt)이 맞는지 확인해 주세요.", file=sys.stderr)
        else:
            if not quiet:
                print(f"[info] 지정한 인증서 번들을 사용합니다: {ca_bundle}")
            return context

    context = ssl.create_default_context()
    if context.cert_store_stats().get("x509_ca", 0) > 0:
        # 저장소가 차 있어도 실제 검증은 실패할 수 있다. 그건 http_get 이 certifi 로
        # 재시도하며 처리한다.
        return context

    alternative = certifi_context()
    if alternative is None:
        if not quiet:
            print("[warn] 신뢰할 루트 인증서를 찾지 못했습니다. HTTPS 연결이 실패할 수 있습니다.", file=sys.stderr)
            print(f"       → {ssl_fix_hint()}", file=sys.stderr)
        return context

    if not quiet:
        print("[info] 시스템 인증서가 비어 있어 certifi 번들을 대신 사용합니다.")
    return alternative


def ssl_fix_hint() -> str:
    """SSL 검증 실패를 어떻게 고치는지 운영체제에 맞춰 한 줄로 알려 준다."""
    if sys.platform == "darwin":
        return (
            "Finder → 응용 프로그램 → 'Python 3.x' 폴더 → 'Install Certificates.command' 를 "
            "더블클릭한 뒤 다시 실행해 주세요. (자세한 내용은 news/START-HERE.md 의 '문제가 생겼을 때')"
        )
    # 파이썬이 여러 개 깔린 컴퓨터에서 엉뚱한 곳에 설치하는 일이 잦아 실행 파일 경로를 그대로 적어 준다.
    install = f'"{sys.executable}" -m pip install --upgrade certifi'
    if os.name == "nt":
        return (
            f"명령 프롬프트에서 `{install}` 를 실행해 보세요. 그래도 안 되면 회사 네트워크의 "
            "보안 프로그램이 통신을 검사하는 경우이니, 전산팀에서 받은 인증서 파일을 "
            "`--ca-bundle 파일경로` 로 지정해 주세요."
        )
    return f"`{install}` 를 실행하거나, 인증서 파일을 `--ca-bundle 파일경로` 로 지정해 주세요."


def is_ssl_error(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", None)
    return isinstance(exc, ssl.SSLError) or isinstance(reason, ssl.SSLError)


def certifi_context() -> ssl.SSLContext | None:
    """certifi 번들을 쓰는 컨텍스트. 설치돼 있지 않으면 None."""
    try:
        import certifi
    except ImportError:
        return None
    try:
        return ssl.create_default_context(cafile=certifi.where())
    except (OSError, ssl.SSLError):
        return None


def http_get(url: str, headers: dict[str, str] | None = None, timeout: float = 12.0) -> bytes:
    """HTTPS GET.

    윈도우는 시스템 인증서 저장소가 비어 있지 않아도 발급기관을 못 찾는 경우가
    있습니다(사내 보안 프로그램, 루트 인증서 자동 업데이트 차단 등). 그래서
    인증서 검증에 실패하면 certifi 번들로 한 번 더 시도하고, 그게 되면 이후로는
    계속 certifi 를 씁니다. 검증 자체를 건너뛰지는 않습니다.
    """
    global SSL_CONTEXT
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as resp:
            return resp.read()
    except urllib.error.URLError as exc:
        if not is_ssl_error(exc):
            raise
        alternative = certifi_context()
        if alternative is None or SSL_CONTEXT is alternative:
            raise
        with urllib.request.urlopen(req, timeout=timeout, context=alternative) as resp:
            body = resp.read()
        if SSL_CONTEXT is not alternative:
            SSL_CONTEXT = alternative
            print("[info] 시스템 인증서로는 검증에 실패해 certifi 번들로 전환했습니다.")
        return body


# --------------------------------------------------------------------------
# 설정
# --------------------------------------------------------------------------
class Target:
    """감시 대상 계열사 1건."""

    __slots__ = ("group_id", "group_name", "group_color", "name", "keyword", "aliases", "excludes")

    def __init__(self, group: dict, company: dict):
        self.group_id = group["id"]
        self.group_name = group["name"]
        self.group_color = group.get("color", "#888888")
        aliases = company.get("aliases") or [company["keyword"]]
        self.name = company.get("name") or aliases[0]
        self.keyword = company["keyword"]
        self.aliases = [normalize(a) for a in aliases if a]
        self.excludes = [normalize(e) for e in company.get("exclude", []) if e]

    def matches(self, text: str) -> bool:
        norm = normalize(text)
        if any(bad in norm for bad in self.excludes):
            return False
        return any(alias in norm for alias in self.aliases)


def load_targets(path: Path) -> tuple[list[Target], list[dict]]:
    config = json.loads(path.read_text(encoding="utf-8"))
    targets: list[Target] = []
    groups: list[dict] = []
    for group in config["groups"]:
        groups.append(
            {
                "id": group["id"],
                "name": group["name"],
                "color": group.get("color", "#888888"),
                "companies": [
                    (c.get("name") or (c.get("aliases") or [c["keyword"]])[0]) for c in group["companies"]
                ],
            }
        )
        for company in group["companies"]:
            targets.append(Target(group, company))
    return targets, groups


# --------------------------------------------------------------------------
# 데이터 소스
# --------------------------------------------------------------------------
class NaverSource:
    """네이버 뉴스 검색 API.

    2026년 네이버가 검색 API 를 개발자센터에서 NAVER API HUB(네이버 클라우드)로
    이관했습니다. 주소와 인증 헤더만 다르고 응답 형식은 같아서, 두 창구를
    variant 로 구분해 같은 코드로 다룹니다.

      hub    - NAVER API HUB. 신규 발급은 이쪽뿐입니다.
      legacy - 기존 개발자센터. 이관 신청을 해 둔 기존 사용자만, 2027-06-30 까지.
    """

    name = "naver"
    VARIANTS = {
        "hub": {
            "url": APIHUB_NEWS,
            "label": "NAVER API HUB",
            "headers": lambda cid, sec: {"X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": sec},
        },
        "legacy": {
            "url": DEVCENTER_NEWS,
            "label": "개발자센터(구)",
            "headers": lambda cid, sec: {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": sec},
        },
    }

    def __init__(self, client_id: str, client_secret: str, display: int = 30, variant: str = "hub"):
        if variant not in self.VARIANTS:
            raise ValueError(f"알 수 없는 창구: {variant}")
        self.client_id = client_id
        self.client_secret = client_secret
        self.display = max(10, min(100, display))
        self.variant = variant

    @property
    def label(self) -> str:
        return self.VARIANTS[self.variant]["label"]

    def request(self, query: str, display: int) -> dict:
        spec = self.VARIANTS[self.variant]
        url = f"{spec['url']}?" + urllib.parse.urlencode(
            {"query": query, "display": display, "start": 1, "sort": "date"}
        )
        body = http_get(url, headers=spec["headers"](self.client_id, self.client_secret))
        return json.loads(body.decode("utf-8"))

    def fetch(self, target: Target) -> list[dict]:
        payload = self.request(target.keyword, self.display)
        items = []
        for raw in payload.get("items", []):
            naver_link = raw.get("link") or ""
            origin = raw.get("originallink") or naver_link
            on_naver = is_naver_news(naver_link)
            items.append(
                {
                    "title": clean_text(raw.get("title", "")),
                    "summary": clean_text(raw.get("description", "")),
                    "link": naver_link or origin,
                    "originalLink": origin,
                    "press": press_of(origin) or press_of(naver_link),
                    "onNaver": on_naver,
                    "pub": parse_pubdate(raw.get("pubDate", "")),
                    "source": self.name,
                }
            )
        return items


class GoogleNewsSource:
    """네이버 API 키가 없을 때 쓰는 폴백. 키 없이 동작하지만 네이버 등록 여부는 알 수 없다."""

    name = "google"

    def fetch(self, target: Target) -> list[dict]:
        url = f"{GOOGLE_RSS}?" + urllib.parse.urlencode(
            {"q": f"{target.keyword} when:2d", "hl": "ko", "gl": "KR", "ceid": "KR:ko"}
        )
        root = ET.fromstring(http_get(url))
        items = []
        for node in root.findall("./channel/item"):
            title = clean_text(node.findtext("title", ""))
            source = node.findtext("{*}source") or node.findtext("source") or ""
            if source and title.endswith(f" - {source}"):
                title = title[: -len(source) - 3].strip()
            link = node.findtext("link", "") or ""
            items.append(
                {
                    "title": title,
                    "summary": clean_text(node.findtext("description", ""))[:300],
                    "link": link,
                    "originalLink": link,
                    "press": source or press_of(link),
                    "onNaver": False,
                    "pub": parse_pubdate(node.findtext("pubDate", "")),
                    "source": self.name,
                }
            )
        return items


# --------------------------------------------------------------------------
# 피드 저장소 + 구독자 브로드캐스트
# --------------------------------------------------------------------------
class FeedStore:
    def __init__(self, max_items: int = 3000, max_seen: int = 20000):
        self.lock = threading.Lock()
        self.items: deque[dict] = deque(maxlen=max_items)
        self.by_key: dict[str, dict] = {}
        self.max_seen = max_seen
        self.subscribers: set[queue.Queue] = set()
        self.status: dict = {
            "startedAt": time.time(),
            "source": None,
            "lastPollAt": None,
            "lastError": None,
            "lastHint": None,
            "notice": None,
            "noticeHint": None,
            "pollCount": 0,
            "errorCount": 0,
            "currentTarget": None,
            "seeded": False,
        }

    # -- 구독 ---------------------------------------------------------------
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=64)
        with self.lock:
            self.subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            self.subscribers.discard(q)

    def broadcast(self, event: str, data) -> None:
        payload = (event, data)
        with self.lock:
            targets = list(self.subscribers)
        for q in targets:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass

    # -- 기사 ---------------------------------------------------------------
    @staticmethod
    def key_of(item: dict) -> str:
        """중복 판정 키.

        네이버 등록 전후로 link 는 바뀌지만 originallink 는 그대로이므로
        원문 링크를 우선 사용한다. 덕분에 '원문 → 네이버 등록' 전환을 같은
        기사로 인식해 중복 알림 대신 '네이버 등록' 이벤트로 처리할 수 있다.
        """
        base = canonical_link(item.get("originalLink", "")) or canonical_link(item.get("link", "")) or normalize(item.get("title", ""))
        return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]

    def add(self, item: dict, target: Target, alert: bool) -> dict | None:
        """기사를 저장한다.

        반환값의 kind 가 무엇이 달라졌는지 알려준다.
          new    - 처음 보는 기사
          naver  - 이미 보던 원문 기사가 네이버뉴스에 등록됨
          update - 같은 기사에 다른 계열사가 추가로 매칭됨
        바뀐 것이 없으면 None.
        """
        key = self.key_of(item)
        now = time.time()
        with self.lock:
            existing = self.by_key.get(key)
            if existing is not None:
                kind = None
                if target.name not in existing["companies"]:
                    existing["companies"].append(target.name)
                    kind = "update"
                if target.group_id not in existing["groupIds"]:
                    existing["groupIds"].append(target.group_id)
                    kind = "update"
                if item["onNaver"] and not existing["onNaver"]:
                    existing["onNaver"] = True
                    existing["link"] = item["link"]
                    existing["naverRegisteredAt"] = now
                    kind = "naver"
                if kind is None:
                    return None
                existing["kind"] = kind
                existing["alert"] = alert and kind == "naver"
                return dict(existing)
            record = {
                "id": key,
                "title": item["title"],
                "summary": item["summary"],
                "link": item["link"],
                "originalLink": item["originalLink"],
                "press": item["press"],
                "onNaver": item["onNaver"],
                "pubDate": item["pub"].isoformat(),
                "pubTs": item["pub"].timestamp(),
                "firstSeen": now,
                "naverRegisteredAt": now if item["onNaver"] else None,
                "groupId": target.group_id,
                "groupName": target.group_name,
                "groupColor": target.group_color,
                "groupIds": [target.group_id],
                "companies": [target.name],
                "source": item["source"],
                "kind": "new",
                "alert": alert,
            }
            self.by_key[key] = record
            self.items.append(record)
            self._prune_locked()
        return record

    def _prune_locked(self) -> None:
        """deque 에서 밀려난 레코드를 인덱스에서도 지운다(호출자가 lock 보유)."""
        if len(self.by_key) <= self.max_seen:
            return
        live = {i["id"] for i in self.items}
        for key in [k for k in self.by_key if k not in live]:
            del self.by_key[key]

    def snapshot(self, since: float | None = None, limit: int = 400) -> list[dict]:
        with self.lock:
            items = list(self.items)
        if since is not None:
            items = [i for i in items if max(i["firstSeen"], i.get("naverRegisteredAt") or 0) > since]
        items.sort(
            key=lambda i: (i["pubTs"], max(i["firstSeen"], i.get("naverRegisteredAt") or 0)),
            reverse=True,
        )
        return items[:limit]

    def get_status(self) -> dict:
        with self.lock:
            status = dict(self.status)
            status["items"] = len(self.items)
            status["subscribers"] = len(self.subscribers)
        return status

    def set_status(self, **kwargs) -> None:
        with self.lock:
            self.status.update(kwargs)

    # -- 영속화 -------------------------------------------------------------
    def save(self, path: Path) -> None:
        try:
            with self.lock:
                data = {"version": 1, "items": list(self.items)[-1500:]}
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            print(f"[warn] 상태 저장 실패: {exc}", file=sys.stderr)

    def load(self, path: Path) -> bool:
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[warn] 상태 복원 실패: {exc}", file=sys.stderr)
            return False
        with self.lock:
            for item in data.get("items", []):
                if not isinstance(item, dict) or "id" not in item:
                    continue
                item["alert"] = False
                item.setdefault("kind", "new")
                item.setdefault("companies", [item["company"]] if item.get("company") else [])
                item.setdefault("groupIds", [item["groupId"]] if item.get("groupId") else [])
                self.items.append(item)
                self.by_key[item["id"]] = item
        return bool(self.by_key)


# --------------------------------------------------------------------------
# 폴러
# --------------------------------------------------------------------------
class Poller(threading.Thread):
    daemon = True

    def __init__(self, store: FeedStore, targets: list[Target], source, args, seeded: bool):
        super().__init__(name="poller")
        self.store = store
        self.targets = targets
        self.source = source
        self.args = args
        self.seeded = seeded
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.max_age = timedelta(hours=args.max_age_hours)
        self._error_key: str | None = None
        self._error_streak = 0
        self._auth_failures = 0

    @property
    def spacing(self) -> float:
        """API 일일 한도를 넘지 않도록 계산한 요청 간격(초)."""
        budget = max(1, self.args.daily_budget)
        by_budget = 86400.0 / budget
        by_cycle = self.args.min_cycle / max(1, len(self.targets))
        return max(by_budget, by_cycle, 0.2)

    def run(self) -> None:
        self.store.set_status(source=self.source.name, seeded=self.seeded)
        index = 0
        last_save = time.time()
        while not self.stop_event.is_set():
            target = self.targets[index % len(self.targets)]
            index += 1
            self.poll_one(target)

            if index % len(self.targets) == 0 and not self.seeded:
                # 첫 한 바퀴는 "이미 있던 기사" 수집 구간이라 알림을 띄우지 않는다.
                self.seeded = True
                self.store.set_status(seeded=True)
                self.store.broadcast("status", self.store.get_status())
                print(f"[info] 초기 수집 완료 — 이제부터 새 기사만 알립니다 ({len(self.store.items)}건 보관)")

            if time.time() - last_save > 30:
                self.store.save(STATE_PATH)
                last_save = time.time()

            self.wake_event.wait(self.spacing)
            self.wake_event.clear()
        self.store.save(STATE_PATH)

    def poll_one(self, target: Target) -> None:
        self.store.set_status(currentTarget=target.name)
        try:
            items = self.source.fetch(target)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200] if exc.fp else ""
            self.note_error(f"http{exc.code}", f"HTTP {exc.code} ({target.keyword}) {detail}", self.HINTS.get(exc.code))
            self.handle_auth_failure(exc.code)
            return
        except (urllib.error.URLError, socket.timeout, ET.ParseError, json.JSONDecodeError, OSError) as exc:
            if is_ssl_error(exc):
                self.note_error("ssl", f"HTTPS 인증서를 확인하지 못했습니다 ({target.keyword}): {exc}", ssl_fix_hint())
            else:
                self.note_error(type(exc).__name__, f"{type(exc).__name__} ({target.keyword}): {exc}",
                                "인터넷 연결을 확인해 주세요.")
            return

        cutoff = datetime.now(KST) - self.max_age
        fresh: list[dict] = []
        updates: list[dict] = []
        for item in items:
            if not item["title"] or not item["link"]:
                continue
            if item["pub"] < cutoff:
                continue
            if self.args.naver_only and self.source.name == "naver" and not item["onNaver"]:
                continue
            if not target.matches(f"{item['title']} {item['summary']}"):
                continue
            record = self.store.add(item, target, alert=self.seeded)
            if not record:
                continue
            (fresh if record["alert"] else updates).append(record)

        self._error_key, self._error_streak = None, 0
        self._auth_failures = 0
        self.store.set_status(
            lastPollAt=time.time(),
            lastError=None,
            lastHint=None,
            pollCount=self.store.get_status()["pollCount"] + 1,
        )
        if fresh:
            fresh.sort(key=lambda i: i["pubTs"], reverse=True)
            for record in fresh:
                if record["kind"] == "naver":
                    tag = "네이버등록"
                elif record["onNaver"]:
                    tag = "네이버"
                else:
                    tag = "원문"
                print(f"[NEW][{record['groupName']}/{'·'.join(record['companies'])}][{tag}] {record['title']}")
            self.store.broadcast("news", fresh)
        if updates and self.seeded:
            # 알림 없이 화면의 기존 카드만 갱신(계열사 태그 추가 등)
            self.store.broadcast("update", updates)
        self.store.broadcast("status", self.store.get_status())

    # 원인별 한 줄 안내. 같은 오류가 반복될 때 화면을 채우지 않도록 한 번만 띄운다.
    HINTS = {
        401: "네이버 API 키가 맞지 않습니다. " + KEY_ISSUE_HINT + " " + KEY_RESET_HINT,
        403: "네이버 API 사용 권한이 없습니다. 개발자센터에서 이 앱에 '검색' API 가 추가되어 있는지 확인해 주세요.",
        429: "오늘 API 호출 한도를 다 썼습니다. 내일 자동으로 풀립니다. (companies.json 에서 계열사를 줄이면 여유가 생깁니다)",
    }

    def handle_auth_failure(self, code: int) -> None:
        """키가 틀린 채로 하루 종일 두드리지 않도록, 몇 번 실패하면 구글 뉴스로 전환한다."""
        if code not in (401, 403) or not isinstance(self.source, NaverSource):
            return
        self._auth_failures += 1
        if self._auth_failures < 3:
            return
        self.source = GoogleNewsSource()
        notice = ("네이버 API 키 인증에 실패해 구글 뉴스로 전환했습니다. "
                  "기사는 계속 모이지만 네이버뉴스 등록 여부는 표시되지 않습니다.")
        self.store.set_status(source=self.source.name, notice=notice, noticeHint=KEY_RESET_HINT)
        print(f"\n[info] {notice}\n       → {KEY_RESET_HINT}\n")

    def note_error(self, key: str, message: str, hint: str | None = None) -> None:
        status = self.store.get_status()
        self.store.set_status(
            lastError=message,
            lastHint=hint,
            lastPollAt=time.time(),
            errorCount=status["errorCount"] + 1,
        )

        # 같은 종류의 오류는 처음 한 번과 그 뒤 20번에 한 번만 출력한다.
        repeat = self._error_streak + 1 if key == self._error_key else 1
        self._error_key, self._error_streak = key, repeat
        if repeat == 1:
            print(f"[warn] {message}", file=sys.stderr)
            if hint:
                print(f"       → {hint}", file=sys.stderr)
        elif repeat % 20 == 0:
            print(f"[warn] 같은 오류가 {repeat}번째 이어지고 있습니다: {key}", file=sys.stderr)
            if hint:
                print(f"       → {hint}", file=sys.stderr)

        self.store.broadcast("status", self.store.get_status())


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "waldo-news/1.0"

    store: FeedStore
    groups: list[dict]
    poller: Poller

    def log_message(self, fmt, *args):  # 요청 로그는 조용히
        pass

    # -- 응답 헬퍼 ----------------------------------------------------------
    def send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload, status: int = 200) -> None:
        self.send_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    # -- 라우팅 -------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parts = urllib.parse.urlsplit(self.path)
        route = parts.path
        params = urllib.parse.parse_qs(parts.query)

        if route in ("/", "/index.html"):
            self.serve_index()
        elif route == "/api/config":
            self.send_json({"groups": self.groups, "status": self.store.get_status()})
        elif route == "/api/news":
            since = params.get("since", [None])[0]
            limit = int(params.get("limit", ["400"])[0])
            self.send_json(
                {
                    "items": self.store.snapshot(float(since) if since else None, limit),
                    "status": self.store.get_status(),
                    "now": time.time(),
                }
            )
        elif route == "/api/status":
            self.send_json(self.store.get_status())
        elif route == "/api/refresh":
            self.poller.wake_event.set()
            self.send_json({"ok": True})
        elif route == "/api/stream":
            self.serve_stream()
        else:
            self.send_json({"error": "not found"}, 404)

    def serve_index(self) -> None:
        try:
            body = INDEX_PATH.read_bytes()
        except OSError:
            self.send_json({"error": f"{INDEX_PATH} 를 찾을 수 없습니다."}, 500)
            return
        self.send_bytes(body, "text/html; charset=utf-8")

    def serve_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        # 본문 길이를 미리 알 수 없는 무한 스트림이라 연결 종료로 경계를 삼는다.
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

        q = self.store.subscribe()
        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.write_event("snapshot", {"items": self.store.snapshot(), "status": self.store.get_status(), "now": time.time()})
            while True:
                try:
                    event, data = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                self.write_event(event, data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.store.unsubscribe(q)

    def write_event(self, event: str, data) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        self.wfile.write(f"event: {event}\ndata: {payload}\n\n".encode("utf-8"))
        self.wfile.flush()


# --------------------------------------------------------------------------
# 엔트리포인트
# --------------------------------------------------------------------------
def _open_browser(url: str) -> None:
    """기본 브라우저로 화면을 띄운다. 실패해도 서버는 그대로 돈다."""
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception as exc:  # noqa: BLE001 - 브라우저가 없거나 열 수 없는 환경
        print(f"[info] 브라우저를 자동으로 열지 못했습니다({exc}). 주소를 직접 입력해 주세요.")


def clean_key(raw: str) -> str:
    """붙여넣기 사고를 정리한다: 앞뒤 공백/줄바꿈, 실수로 딸려온 따옴표."""
    return (raw or "").strip().strip("'\"").strip()


def check_key_shape(client_id: str, client_secret: str) -> str | None:
    """넣기 전에 눈에 띄는 문제만 짚어 준다(막지는 않는다).

    어느 창구의 키인지는 생김새로 단정할 수 없어, 최종 판정은 실제 호출로 합니다
    (resolve_naver_source). 여기서는 흔한 착오만 알려 줍니다.
    """
    if " " in client_id or " " in client_secret:
        return "키 안에 공백이 들어 있습니다. 앞뒤 공백까지 복사되지 않았는지 확인해 주세요."
    # 'ncp_iam_' 은 계정 전체용 Access Key 라서 검색 API 인증에는 쓰이지 않는다.
    # API HUB 의 [인증 정보] 에서 주는 Client ID/Secret 과 헷갈리기 쉽다.
    for label, value in (("Client ID", client_id), ("Client Secret", client_secret)):
        if value.lower().startswith("ncp_iam_"):
            return (f"{label} 가 '계정관리 → 인증키 관리' 의 Access Key(ncp_iam_...) 로 보입니다. "
                    f"검색 API 에는 이 키가 아니라 API HUB 의 인증 정보를 씁니다. {KEY_ISSUE_HINT}")
    return None


def load_saved_key() -> tuple[str, str]:
    """이 컴퓨터에 저장해 둔 API 키를 읽는다."""
    try:
        data = json.loads(KEY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", ""
    return clean_key(str(data.get("client_id", ""))), clean_key(str(data.get("client_secret", "")))


def save_key(client_id: str, client_secret: str) -> None:
    try:
        KEY_PATH.write_text(
            json.dumps({"client_id": client_id, "client_secret": client_secret}, indent=2),
            encoding="utf-8",
        )
        os.chmod(KEY_PATH, 0o600)  # 다른 사용자 계정에서 못 읽게
    except OSError as exc:
        print(f"[warn] 키를 저장하지 못했습니다: {exc}", file=sys.stderr)
    else:
        print(f"[info] 키를 저장했습니다. 다음부터는 안 물어봅니다. ({KEY_PATH.name})")


def prompt_for_key() -> tuple[str, str]:
    """처음 실행이면 화면에서 직접 키를 받는다. 그냥 Enter 를 치면 건너뛴다."""
    print()
    print("=" * 66)
    print(" 네이버 검색 API 키가 아직 없습니다.")
    print()
    print(" 키를 넣으면: 네이버뉴스 등록 여부까지 정확하게 실시간으로 확인됩니다.")
    print(" 키가 없으면: 구글 뉴스 RSS로 대신 동작합니다(네이버 등록 여부는 표시 안 됨).")
    print()
    print(" 키 받는 법 (몇 분, 현재 무료)")
    print("   네이버가 2026년에 검색 API 를 개발자센터에서 NAVER API HUB 로 옮겼습니다.")
    print("   신규 발급은 아래 경로뿐입니다.")
    print("   1. https://www.ncloud.com 접속 후 로그인 (네이버 클라우드 플랫폼)")
    print("   2. 콘솔 → All Services → Application Services → NAVER API HUB")
    print("   3. Application 을 만들고, 쓸 API 로 [검색] 을 선택")
    print("   4. API 관리의 [인증 정보] 버튼 → Client ID / Client Secret 복사")
    print("   ※ '계정관리 → 인증키 관리' 의 ncp_iam_ 로 시작하는 Access Key 가 아닙니다.")
    print("     그건 클라우드 전체용 키라 검색 API 인증에는 쓰이지 않습니다.")
    print("   ※ 예전 개발자센터 키를 이미 쓰고 계셨다면 그대로 넣으셔도 됩니다(2027-06-30 까지).")
    print()
    print(" ※ 그냥 Enter 를 누르면 키 없이 시작합니다. 나중에 넣어도 됩니다.")
    print("=" * 66)
    try:
        client_id = clean_key(input(" Client ID     : "))
        if not client_id:
            return "", ""
        client_secret = clean_key(input(" Client Secret : "))
    except (EOFError, KeyboardInterrupt):
        print()
        return "", ""
    if not client_secret:
        return "", ""

    warning = check_key_shape(client_id, client_secret)
    if warning:
        print(f"\n [!] {warning}")
        print("     그래도 이대로 진행하려면 Enter, 다시 입력하려면 아무 글자나 치고 Enter.")
        try:
            if input("     > ").strip():
                return prompt_for_key()
        except (EOFError, KeyboardInterrupt):
            print()

    save_key(client_id, client_secret)
    return client_id, client_secret


def resolve_naver_source(client_id: str, client_secret: str, display: int, api: str) -> NaverSource | None:
    """이 키가 어느 창구의 것인지 실제로 한 번 물어보고 정한다.

    API HUB 키와 개발자센터 키는 생김새로 구분할 수 없어서, 짧은 조회를 한 번씩
    보내 보고 통과하는 쪽을 씁니다. 둘 다 인증에 실패하면 None(=구글 뉴스로 시작).
    네트워크 문제로 판별을 못 하면 신규 발급처인 API HUB 를 택하고, 이후 401 이
    반복되면 폴러가 알아서 구글 뉴스로 넘깁니다.
    """
    if api != "auto":
        return NaverSource(client_id, client_secret, display, variant=api)

    candidates = list(NaverSource.VARIANTS)
    rejected: list[str] = []
    for variant in candidates:
        source = NaverSource(client_id, client_secret, display, variant=variant)
        try:
            source.request("현대엘리베이터", 1)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                rejected.append(source.label)
                continue
            return source  # 인증은 통과했고 다른 문제(한도 등)이므로 이 창구가 맞다
        except Exception as exc:  # noqa: BLE001 - 네트워크 문제면 판별을 포기한다
            print(f"[warn] 어느 API 창구인지 확인하지 못했습니다({type(exc).__name__}). "
                  f"{NaverSource.VARIANTS['hub']['label']} 로 시작합니다.", file=sys.stderr)
            return NaverSource(client_id, client_secret, display, variant="hub")
        else:
            return source

    print(f"[warn] 넣어 두신 키가 {' 와 '.join(rejected)} 양쪽 모두에서 거부되었습니다.", file=sys.stderr)
    print(f"       → {KEY_ISSUE_HINT}", file=sys.stderr)
    return None


def build_source(args) -> object:
    client_id = clean_key(args.client_id or os.environ.get("NAVER_CLIENT_ID", ""))
    client_secret = clean_key(args.client_secret or os.environ.get("NAVER_CLIENT_SECRET", ""))
    if not (client_id and client_secret):
        client_id, client_secret = load_saved_key()
    if not (client_id and client_secret) and sys.stdin and sys.stdin.isatty():
        client_id, client_secret = prompt_for_key()

    if client_id and client_secret:
        source = resolve_naver_source(client_id, client_secret, args.display, args.api)
        if source is not None:
            print(f"[info] 데이터 소스: 네이버 검색 API ({source.label})")
            return source
    print(
        "[info] 데이터 소스: 구글 뉴스 RSS (키 없이 동작하는 대체 경로)\n"
        "       네이버뉴스 등록 여부까지 보려면 네이버 API 키가 필요합니다.\n"
        "       넣는 방법은 news/START-HERE.md 의 '4단계'를 보세요."
    )
    return GoogleNewsSource()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CJ/HDC/현대엘리베이터 계열사 네이버뉴스 실시간 모니터")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--client-id", default="", help="네이버 검색 API Client ID")
    parser.add_argument("--client-secret", default="", help="네이버 검색 API Client Secret")
    parser.add_argument("--display", type=int, default=30, help="키워드당 조회 건수(10~100)")
    parser.add_argument("--daily-budget", type=int, default=20000, help="하루 API 호출 상한(네이버 무료 한도 25,000)")
    parser.add_argument("--min-cycle", type=float, default=90.0, help="전체 계열사를 한 바퀴 도는 최소 시간(초)")
    parser.add_argument("--max-age-hours", type=float, default=48.0, help="이 시간보다 오래된 기사는 무시")
    parser.add_argument("--naver-only", action="store_true", help="네이버뉴스에 등록된 기사만 수집")
    parser.add_argument("--reset", action="store_true", help="저장된 상태를 지우고 처음부터 시작")
    parser.add_argument("--set-key", action="store_true", help="네이버 API 키를 새로 입력해서 저장")
    parser.add_argument("--no-open", action="store_true", help="시작할 때 브라우저를 자동으로 열지 않음")
    parser.add_argument("--ca-bundle", default="", help="HTTPS 검증에 쓸 인증서 파일(.pem/.crt) 경로")
    parser.add_argument("--api", choices=["auto", "hub", "legacy"], default="auto",
                        help="검색 API 창구: auto(자동 판별), hub(NAVER API HUB), legacy(구 개발자센터)")
    parser.add_argument("--doctor", action="store_true", help="연결 상태를 점검하고 문제 원인을 알려 줍니다")
    return parser.parse_args(argv)


def run_doctor(args) -> int:
    """뉴스를 못 가져올 때 원인을 짚어 준다. 초보자용 자가 진단."""
    import platform

    print("=" * 66)
    print(" 연결 점검")
    print("=" * 66)
    print(f" 파이썬  : {sys.version.split()[0]} ({platform.python_implementation()})")
    print(f" 실행파일: {sys.executable}")
    print("           ↑ certifi 를 설치했다면 이 경로의 파이썬에 설치했는지 확인하세요.")
    print(f" 운영체제: {platform.system()} {platform.release()}")

    context = build_ssl_context(args.ca_bundle, quiet=True)
    ca_count = context.cert_store_stats().get("x509_ca", 0)
    print(f" 인증서  : 시스템 저장소 {ca_count}개", end="")
    if args.ca_bundle:
        print(f" / 지정한 파일 사용: {args.ca_bundle}")
    elif ca_count == 0:
        print(" ← 비어 있습니다. 이게 원인일 가능성이 큽니다.")
    else:
        print()
    alternative = certifi_context()
    print(f" certifi : {'설치됨' if alternative else '없음 (pip install certifi 로 설치 가능)'}")

    client_id, client_secret = (
        args.client_id or os.environ.get("NAVER_CLIENT_ID", ""),
        args.client_secret or os.environ.get("NAVER_CLIENT_SECRET", ""),
    )
    if not (client_id and client_secret):
        client_id, client_secret = load_saved_key()
    print(f" API 키  : {'있음' if client_id and client_secret else '없음 (구글 뉴스로 동작)'}")
    print()

    global SSL_CONTEXT
    SSL_CONTEXT = context

    checks: list[tuple[str, str, dict[str, str]]] = [
        ("구글 뉴스 RSS", f"{GOOGLE_RSS}?q=test&hl=ko&gl=KR&ceid=KR:ko", {}),
    ]
    if client_id and client_secret:
        # 어느 창구의 키인지 모르니 둘 다 시험한다. 하나만 되면 그게 답이다.
        for variant, spec in NaverSource.VARIANTS.items():
            checks.append((
                f"네이버 검색 API · {spec['label']}",
                f"{spec['url']}?" + urllib.parse.urlencode({"query": "현대엘리베이터", "display": 1}),
                spec["headers"](client_id, client_secret),
            ))

    failures = 0
    for label, url, headers in checks:
        print(f" [{label}] 확인 중...", end=" ", flush=True)
        try:
            body = http_get(url, headers=headers, timeout=15.0)
        except urllib.error.HTTPError as exc:
            failures += 1
            print(f"실패 (HTTP {exc.code})")
            hint = Poller.HINTS.get(exc.code)
            print(f"    → {hint}" if hint else f"    → 응답: {exc.read()[:200].decode('utf-8', 'replace')}")
        except Exception as exc:  # noqa: BLE001 - 무엇이 나오든 원인을 알려 주는 게 목적
            failures += 1
            print("실패")
            print(f"    {type(exc).__name__}: {exc}")
            if is_ssl_error(exc):
                if alternative is None:
                    print("    → certifi 가 설치되어 있지 않습니다. 먼저 이걸 해보세요:")
                    print(f"       {sys.executable} -m pip install --upgrade certifi")
                else:
                    print("    → certifi 로도 검증에 실패했습니다. 사내 보안 프로그램이 통신을")
                    print("       가로채는 환경으로 보입니다. 전산팀에서 인증서 파일(.crt/.pem)을 받아")
                    print("       `--ca-bundle 파일경로` 로 지정해 주세요.")
                print(f"    → {ssl_fix_hint()}")
            else:
                print("    → 인터넷 연결을 확인해 주세요. 회사 네트워크라면 방화벽일 수 있습니다.")
        else:
            note = " (certifi 번들로 성공)" if SSL_CONTEXT is not context else ""
            print(f"성공 ({len(body):,} 바이트){note}")

    print()
    if failures:
        print(f" 점검 결과: {failures}건 실패. 위 '→' 안내를 먼저 해보세요.")
    else:
        print(" 점검 결과: 이상 없습니다. 그냥 실행하시면 됩니다.")
    print("=" * 66)
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.doctor:
        return run_doctor(args)

    global SSL_CONTEXT
    SSL_CONTEXT = build_ssl_context(args.ca_bundle)

    targets, groups = load_targets(CONFIG_PATH)
    if not targets:
        print("[error] companies.json 에 감시 대상이 없습니다.", file=sys.stderr)
        return 1

    if args.reset:
        STATE_PATH.unlink(missing_ok=True)
    if args.set_key:
        KEY_PATH.unlink(missing_ok=True)

    store = FeedStore()
    seeded = store.load(STATE_PATH)
    source = build_source(args)
    poller = Poller(store, targets, source, args, seeded)

    handler = type("BoundHandler", (Handler,), {"store": store, "groups": groups, "poller": poller})
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    httpd.daemon_threads = True

    poller.start()
    url = f"http://{args.host}:{args.port}/"
    print(f"[info] 감시 대상 {len(targets)}개 계열사 / 요청 간격 {poller.spacing:.1f}초 "
          f"(한 바퀴 약 {poller.spacing * len(targets):.0f}초)")
    if not seeded:
        print("[info] 첫 한 바퀴는 기존 기사를 모으는 중이라 알림이 뜨지 않습니다.")
    print()
    print("=" * 66)
    print(f" 준비 끝! 브라우저에서 이 주소를 여세요 →  {url}")
    print()
    print(" · 이 창을 닫으면 뉴스 수집도 멈춥니다. 켜 둔 채로 두세요.")
    print(" · 끝내려면 이 창에서 Ctrl+C 를 누르거나 창을 닫으면 됩니다.")
    print(f" · 이 주소는 이 컴퓨터에서만 열립니다({args.host}). 인터넷에 공개되지 않습니다.")
    print("=" * 66)

    if not args.no_open:
        threading.Timer(1.0, lambda: _open_browser(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[info] 종료합니다.")
    finally:
        poller.stop_event.set()
        poller.wake_event.set()
        store.save(STATE_PATH)
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
