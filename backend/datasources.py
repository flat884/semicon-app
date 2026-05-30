"""
データ取得層。無料ソースからの取得を一元管理し、サーバ側キャッシュで外部アクセスを最小化する。

ソース:
- 株価/指標: yfinance（Yahoo! Finance 経由・約15分遅延・非公式）
- 開示    : EDINET API v2（金融庁・法定開示）
- ニュース : Google ニュース RSS（見出し+リンクのみ）

重要(規約):
- yfinance は非公式エンドポイント。私的利用前提。公開時は JPX 15分遅延API等へ要切替。
- 過度な高頻度アクセスは IP ブロックの恐れ。バッチ＋インターバルで保護。
- ニュースは見出しとリンクのみ保持し、本文は転載しない。
"""
import time
import threading
import datetime as dt
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from typing import Optional

import requests

try:
    import yfinance as yf
except Exception:  # ライブラリ未導入でもサーバは起動できるように
    yf = None

from universe import UNIVERSE, by_code

# ───────────────────────── 設定 ─────────────────────────
BATCH_SIZE = 8           # yfinance 一括取得のバッチサイズ（ブロック回避）
BATCH_INTERVAL = 1.0     # バッチ間インターバル秒
PRICE_TTL = 60           # 株価キャッシュ寿命（秒）。ポーリング下限の目安
NEWS_TTL = 180           # ニュースキャッシュ寿命（秒）。3分に短縮し新着に早く気づく
DISC_TTL = 180           # 適時開示キャッシュ寿命（秒）。3分に短縮
EDINET_DISC_TTL = 1800   # EDINET法定開示は更新が遅いので30分でよい
TDNET_API = "https://webapi.yanoshin.jp/webapi/tdnet/list"  # やのしん氏 TDnet WEB-API（個人運営・要常識的利用）
EDINET_API = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
USER_AGENT = "SemiconScope/1.0 (personal research tool)"


@dataclass
class Quote:
    code: str
    price: Optional[float] = None
    prev_close: Optional[float] = None
    volume: Optional[float] = None
    per: Optional[float] = None
    market_cap: Optional[float] = None
    dividend_yield: Optional[float] = None
    history: list = field(default_factory=list)   # 直近終値リスト（スパークライン用）
    ok: bool = False
    fetched_at: Optional[str] = None

    @property
    def change_pct(self):
        if self.price and self.prev_close:
            return (self.price - self.prev_close) / self.prev_close * 100
        return None


class _Cache:
    def __init__(self):
        self._d = {}
        self._lock = threading.Lock()

    def get(self, key, ttl):
        with self._lock:
            v = self._d.get(key)
            if v and (time.time() - v[0]) < ttl:
                return v[1]
        return None

    def set(self, key, value):
        with self._lock:
            self._d[key] = (time.time(), value)


cache = _Cache()


# ───────────────────────── 株価 (yfinance) ─────────────────────────
def _now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def fetch_quotes(codes: list[str]) -> dict[str, Quote]:
    """指定コード群の株価をyfinanceでバッチ取得。キャッシュ優先。"""
    result: dict[str, Quote] = {}
    to_fetch = []
    for c in codes:
        cached = cache.get(f"q:{c}", PRICE_TTL)
        if cached:
            result[c] = cached
        else:
            to_fetch.append(c)

    if not to_fetch or yf is None:
        # yfinance未導入時は空Quoteを返す（サーバは落とさない）
        for c in to_fetch:
            result[c] = Quote(code=c, ok=False, fetched_at=_now_iso())
        return result

    # バッチ＋インターバルで取得（IPブロック回避）
    for i in range(0, len(to_fetch), BATCH_SIZE):
        batch = to_fetch[i:i + BATCH_SIZE]
        for c in batch:
            q = _fetch_one(c)
            cache.set(f"q:{c}", q)
            result[c] = q
        if i + BATCH_SIZE < len(to_fetch):
            time.sleep(BATCH_INTERVAL)
    return result


def _fetch_one(code: str) -> Quote:
    """1銘柄分の株価・指標・直近終値を取得。"""
    q = Quote(code=code, fetched_at=_now_iso())
    try:
        t = yf.Ticker(f"{code}.T")
        # 直近2か月の日足（前日終値とスパークライン用）
        hist = t.history(period="2mo", interval="1d")
        if hist is None or hist.empty:
            # 空DataFrame = データなし or ブロック。区別不能なので ok=False のまま返す
            return q
        closes = [round(float(x), 1) for x in hist["Close"].dropna().tolist()]
        q.history = closes[-30:]
        if len(closes) >= 2:
            q.price = closes[-1]
            q.prev_close = closes[-2]
        vol = hist["Volume"].dropna().tolist()
        q.volume = float(vol[-1]) if vol else None

        # 指標は info から（取れないことがあるため防御的に）
        try:
            info = t.info
            q.per = info.get("trailingPE")
            q.market_cap = info.get("marketCap")
            dy = info.get("dividendYield")
            q.dividend_yield = (dy * 100) if dy else None
            # current price があれば優先（より新しい）
            cp = info.get("currentPrice")
            if cp:
                q.price = float(cp)
        except Exception:
            pass

        q.ok = q.price is not None
    except Exception:
        q.ok = False
    return q


# ───────────────────────── 開示 (EDINET) ─────────────────────────
def fetch_recent_disclosures(days: int = 5) -> list[dict]:
    """EDINET から直近 days 日の提出書類一覧を取得し、半導体ユニバースのものへ簡易フィルタ。

    EDINET の検索キーは社名突き合わせが必要なため、ここでは「提出書類一覧を取得し、
    フロントで銘柄名一致のものを引く」前提の素のリストを返す。
    """
    key = f"disc:{days}"
    cached = cache.get(key, EDINET_DISC_TTL)
    if cached:
        return cached

    out = []
    today = dt.date.today()
    for d in range(days):
        day = today - dt.timedelta(days=d)
        try:
            r = requests.get(
                EDINET_API,
                params={"date": day.isoformat(), "type": 2},
                headers={"User-Agent": USER_AGENT},
                timeout=15,
            )
            if r.status_code != 200:
                continue
            js = r.json()
            for doc in js.get("results", []):
                out.append({
                    "date": day.isoformat(),
                    "filer": doc.get("filerName"),
                    "doc_type": doc.get("docDescription"),
                    "sec_code": (doc.get("secCode") or "")[:4],
                    "doc_id": doc.get("docID"),
                })
        except Exception:
            continue
    cache.set(key, out)
    return out


def fetch_tdnet(code: str, limit: int = 10) -> list[dict]:
    """TDnet 適時開示を銘柄コード指定で取得（やのしん氏 WEB-API・無料）。

    決算短信・業績修正・自己株買い等、急騰急落の引き金になりやすい開示を速報で拾う。
    個人運営APIのため、キャッシュ必須＋失敗時は静かに空を返す（常識的利用）。
    """
    key = f"tdnet:{code}"
    cached = cache.get(key, DISC_TTL)
    if cached is not None:
        return cached

    out = []
    try:
        url = f"{TDNET_API}/{code}.json?limit={limit}"
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        if r.status_code == 200:
            js = r.json()
            for item in js.get("items", []):
                td = item.get("Tdnet") or {}
                if not td:
                    continue
                out.append({
                    "date": (td.get("pubdate") or "")[:16],
                    "doc_type": td.get("title"),
                    "url": td.get("document_url"),
                    "source": "TDnet",
                })
    except Exception:
        pass
    cache.set(key, out)
    return out


def disclosures_for(code: str, name: str, days: int = 5) -> list[dict]:
    """銘柄に紐づく開示を、TDnet適時開示（速報）→EDINET法定開示 の順で統合。"""
    out = []
    # 1) TDnet 適時開示（速報性が高い・銘柄別に直接取得）
    out.extend(fetch_tdnet(code))
    # 2) EDINET 法定開示（証券コード or 社名一致）
    for d in fetch_recent_disclosures(days):
        if (d.get("sec_code") and d["sec_code"] == code) or \
           (d.get("filer") and name[:4] in (d["filer"] or "")):
            out.append({
                "date": d["date"],
                "doc_type": d.get("doc_type") or "提出書類",
                "url": None,
                "source": "EDINET",
                "doc_id": d.get("doc_id"),
            })
    return out[:12]


def has_recent_disclosure(code: str, within_hours: int = 24) -> bool:
    """直近 within_hours 時間内に適時開示があったか（アラート用）。"""
    items = fetch_tdnet(code, limit=5)
    if not items:
        return False
    now = dt.datetime.now()
    for it in items:
        ds = it.get("date", "")
        try:
            # pubdate 例: "2026-05-29 15:30" を許容
            t = dt.datetime.fromisoformat(ds.replace("/", "-")[:16])
            if (now - t).total_seconds() <= within_hours * 3600:
                return True
        except Exception:
            continue
    return False


# ───────────────────────── ニュース (Google News RSS) ─────────────────────────
def fetch_news(name: str, limit: int = 6) -> list[dict]:
    """Google ニュース RSS から見出し＋リンクを取得（本文は保持しない）。"""
    key = f"news:{name}"
    cached = cache.get(key, NEWS_TTL)
    if cached:
        return cached

    out = []
    try:
        q = requests.utils.quote(f"{name} 半導体")
        url = f"https://news.google.com/rss/search?q={q}&hl=ja&gl=JP&ceid=JP:ja"
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for item in root.iter("item"):
                title = item.findtext("title")
                link = item.findtext("link")
                pub = item.findtext("pubDate")
                src_el = item.find("{*}source")
                source = src_el.text if src_el is not None else "news"
                out.append({"title": title, "link": link, "pub": pub, "source": source})
                if len(out) >= limit:
                    break
    except Exception:
        pass
    cache.set(key, out)
    return out
