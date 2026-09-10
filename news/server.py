#!/usr/bin/env python3
"""CJ / HDC / 현대엘리베이터 계열사 네이버뉴스 실시간 모니터.

표준 라이브러리만 사용합니다(설치 불필요).

    python3 news/server.py

서버가 백그라운드에서 계열사 키워드를 계속 검색하고, 새 기사가 네이버뉴스에
등록되는 즉시 SSE(Server-Sent Events)로 브라우저에 밀어 넣습니다.

데이터 소스
  1) 네이버 검색 API (권장) - NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수
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

KST = timezone(timedelta(hours=9))
NAVER_API = "https://openapi.naver.com/v1/search/news.json"
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


def http_get(url: str, headers: dict[str, str] | None = None, timeout: float = 12.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


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
    name = "naver"

    def __init__(self, client_id: str, client_secret: str, display: int = 30):
        self.client_id = client_id
        self.client_secret = client_secret
        self.display = max(10, min(100, display))

    def fetch(self, target: Target) -> list[dict]:
        url = f"{NAVER_API}?" + urllib.parse.urlencode(
            {"query": target.keyword, "display": self.display, "start": 1, "sort": "date"}
        )
        body = http_get(
            url,
            headers={
                "X-Naver-Client-Id": self.client_id,
                "X-Naver-Client-Secret": self.client_secret,
            },
        )
        payload = json.loads(body.decode("utf-8"))
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
            self.note_error(f"HTTP {exc.code} ({target.keyword}) {detail}")
            return
        except (urllib.error.URLError, socket.timeout, ET.ParseError, json.JSONDecodeError, OSError) as exc:
            self.note_error(f"{type(exc).__name__} ({target.keyword}): {exc}")
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

        self.store.set_status(
            lastPollAt=time.time(),
            lastError=None,
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

    def note_error(self, message: str) -> None:
        status = self.store.get_status()
        self.store.set_status(
            lastError=message,
            lastPollAt=time.time(),
            errorCount=status["errorCount"] + 1,
        )
        print(f"[warn] {message}", file=sys.stderr)
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
def build_source(args) -> object:
    client_id = args.client_id or os.environ.get("NAVER_CLIENT_ID", "")
    client_secret = args.client_secret or os.environ.get("NAVER_CLIENT_SECRET", "")
    if client_id and client_secret:
        print("[info] 데이터 소스: 네이버 검색 API")
        return NaverSource(client_id, client_secret, args.display)
    print(
        "[info] 데이터 소스: 구글 뉴스 RSS (폴백)\n"
        "       네이버 뉴스 등록 여부까지 정확히 보려면 https://developers.naver.com 에서\n"
        "       애플리케이션을 등록하고 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 를 설정하세요."
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    targets, groups = load_targets(CONFIG_PATH)
    if not targets:
        print("[error] companies.json 에 감시 대상이 없습니다.", file=sys.stderr)
        return 1

    if args.reset:
        STATE_PATH.unlink(missing_ok=True)

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
    print(f"[info] 브라우저에서 열기 → {url}")
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
