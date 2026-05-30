"""
SEMICON SCOPE バックエンド (FastAPI)

エンドポイント:
  GET /api/stocks            一覧（全銘柄の株価・前日比・出来高・PER・テーマ・遅延メタ）
  GET /api/stocks/{code}     詳細（指標・チャート・ニュース・開示）
  GET /api/alerts            アラート（急騰/急落/出来高急増）
  GET /api/meta              データ遅延種別・最終更新時刻

起動: uvicorn main:app --reload --port 8000
クライアントは外部ソースを直接叩かず、必ず本APIのキャッシュ越しに取得する。
"""
import datetime as dt
import statistics

import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from universe import UNIVERSE, THEME_LABELS, SUBCATEGORIES, by_code
import datasources as ds

app = FastAPI(title="SEMICON SCOPE API", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # ローカル開発用。公開時は絞る
    allow_methods=["*"],
    allow_headers=["*"],
)

# アラートしきい値（設定可能）
SURGE_PCT = 5.0       # 急騰 +5%以上
PLUNGE_PCT = -4.0     # 急落 -4%以下
VOL_SPIKE_X = 1.8     # 出来高が平均比 1.8倍以上

DELAY_LABEL = "15分遅延"   # yfinance(Yahoo!)由来。モードBではJPX 15分遅延APIに置換
_last_update = {"ts": None}


def _serialize(stock, q: ds.Quote):
    return {
        "code": stock["code"],
        "name": stock["name"],
        "sub": stock["sub"],
        "themes": stock["themes"],
        "theme_labels": [THEME_LABELS[t] for t in stock["themes"]],
        "ipo": stock["ipo"],
        "price": q.price,
        "prev_close": q.prev_close,
        "change_pct": round(q.change_pct, 2) if q.change_pct is not None else None,
        "volume": q.volume,
        "per": round(q.per, 1) if q.per else None,
        "market_cap": q.market_cap,
        "dividend_yield": round(q.dividend_yield, 2) if q.dividend_yield else None,
        "ok": q.ok,
        "fetched_at": q.fetched_at,
    }


@app.get("/api/meta")
def meta():
    return {
        "delay_label": DELAY_LABEL,
        "last_update": _last_update["ts"],
        "now": dt.datetime.now().isoformat(timespec="seconds"),
        "source": "yfinance(株価) / EDINET(開示) / Google News RSS(見出し)",
        "disclaimer": "本アプリは情報収集・参考目的であり、投資判断・売買推奨を行うものではありません。",
        "subcategories": SUBCATEGORIES,
        "theme_labels": THEME_LABELS,
    }


@app.get("/api/stocks")
def list_stocks():
    codes = [s["code"] for s in UNIVERSE]
    quotes = ds.fetch_quotes(codes)
    _last_update["ts"] = dt.datetime.now().isoformat(timespec="seconds")
    rows = [_serialize(s, quotes[s["code"]]) for s in UNIVERSE]
    # IPO を先頭へ、その後 出来高降順
    rows.sort(key=lambda r: (r["volume"] or 0), reverse=True)
    rows.sort(key=lambda r: 0 if r["ipo"] else 1)
    return {"delay_label": DELAY_LABEL, "last_update": _last_update["ts"], "stocks": rows}


@app.get("/api/stocks/{code}")
def stock_detail(code: str):
    stock = by_code(code)
    if not stock:
        raise HTTPException(404, "unknown code")
    q = ds.fetch_quotes([code])[code]
    base = _serialize(stock, q)
    base["history"] = q.history
    base["news"] = ds.fetch_news(stock["name"])
    base["disclosures"] = ds.disclosures_for(code, stock["name"])
    return base


@app.get("/api/alerts")
def alerts():
    codes = [s["code"] for s in UNIVERSE]
    quotes = ds.fetch_quotes(codes)
    vols = [quotes[c].volume for c in codes if quotes[c].volume]
    avg_vol = statistics.mean(vols) if vols else 0
    out = []
    for s in UNIVERSE:
        q = quotes[s["code"]]
        cp = q.change_pct
        if cp is None:
            continue
        if cp >= SURGE_PCT:
            out.append({"code": s["code"], "name": s["name"], "type": "surge",
                        "label": f"▲ {s['name']} 急騰 +{cp:.1f}%"})
        elif cp <= PLUNGE_PCT:
            out.append({"code": s["code"], "name": s["name"], "type": "plunge",
                        "label": f"▼ {s['name']} 急落 {cp:.1f}%"})
        if avg_vol and q.volume and q.volume >= avg_vol * VOL_SPIKE_X:
            out.append({"code": s["code"], "name": s["name"], "type": "volume",
                        "label": f"◆ {s['name']} 出来高急増 {q.volume/1e6:.0f}M"})
    # 適時開示が直近24h以内に出た銘柄（決算・業績修正など。気づきの速さに直結）
    for s in UNIVERSE:
        try:
            if ds.has_recent_disclosure(s["code"], within_hours=24):
                out.append({"code": s["code"], "name": s["name"], "type": "disclosure",
                            "label": f"📄 {s['name']} 新規開示あり"})
        except Exception:
            continue
    return {"alerts": out, "last_update": _last_update["ts"]}


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ─── フロント配信（1サービスで完結。Render無料枠1つに収める）───
# frontend ディレクトリを複数候補から探索（ローカル/Render/Docker いずれでも動くように）
def _find_frontend():
    env = os.environ.get("FRONTEND_DIR")
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        env,
        os.path.join(os.path.dirname(here), "frontend"),  # ../frontend（標準構成）
        os.path.join(here, "frontend"),                    # ./frontend（同梱した場合）
        "/app/frontend",                                   # Docker
    ]
    for c in candidates:
        if c and os.path.exists(os.path.join(c, "index.html")):
            return c
    return os.path.join(os.path.dirname(here), "frontend")

_FRONTEND_DIR = _find_frontend()
_INDEX = os.path.join(_FRONTEND_DIR, "index.html")


@app.get("/")
def serve_index():
    if os.path.exists(_INDEX):
        return FileResponse(_INDEX)
    return {"service": "SEMICON SCOPE API", "docs": "/docs",
            "endpoints": ["/api/stocks", "/api/stocks/{code}", "/api/alerts", "/api/meta"]}


# アイコン・manifest 等の静的ファイルを frontend/ から配信
_ALLOWED_STATIC = {"manifest.json", "icon-180.png", "icon-512.png"}


@app.get("/{filename}")
def serve_static(filename: str):
    # 許可リスト方式でパストラバーサルを防ぐ
    if filename in _ALLOWED_STATIC:
        path = os.path.join(_FRONTEND_DIR, filename)
        if os.path.exists(path):
            return FileResponse(path)
    raise HTTPException(404, "not found")
