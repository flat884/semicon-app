"""
半導体関連銘柄マスタ（日本株）。
- code  : 証券コード（4桁。yfinance では code + ".T"）
- name  : 社名
- sub   : サブカテゴリ（製造装置/材料/設計/検査/パワー半導体/後工程）
- themes: テーマタグ（ai=生成AI / car=車載 / pow=パワー半導体）
- ipo   : 新規上場フラグ（JPX 新規上場会社情報／EDINET 目論見書で要更新）
"""

UNIVERSE = [
    {"code": "8035", "name": "東京エレクトロン",          "sub": "製造装置",     "themes": ["ai"],         "ipo": False},
    {"code": "7735", "name": "SCREENホールディングス",    "sub": "製造装置",     "themes": ["ai"],         "ipo": False},
    {"code": "6857", "name": "アドバンテスト",            "sub": "検査",         "themes": ["ai"],         "ipo": False},
    {"code": "6920", "name": "レーザーテック",            "sub": "検査",         "themes": ["ai"],         "ipo": False},
    {"code": "7729", "name": "東京精密",                  "sub": "検査",         "themes": [],             "ipo": False},
    {"code": "6146", "name": "ディスコ",                  "sub": "後工程",       "themes": ["ai"],         "ipo": False},
    {"code": "6315", "name": "TOWA",                      "sub": "後工程",       "themes": ["ai"],         "ipo": False},
    {"code": "6967", "name": "新光電気工業",              "sub": "後工程",       "themes": ["ai"],         "ipo": False},
    {"code": "4063", "name": "信越化学工業",              "sub": "材料",         "themes": ["car"],        "ipo": False},
    {"code": "4004", "name": "レゾナック・ホールディングス","sub": "材料",        "themes": ["ai", "car"],  "ipo": False},
    {"code": "6981", "name": "村田製作所",                "sub": "材料",         "themes": ["car"],        "ipo": False},
    {"code": "6762", "name": "TDK",                       "sub": "材料",         "themes": ["car"],        "ipo": False},
    {"code": "6526", "name": "ソシオネクスト",            "sub": "設計",         "themes": ["ai", "car"],  "ipo": False},
    {"code": "6723", "name": "ルネサスエレクトロニクス",  "sub": "設計",         "themes": ["car", "ai"],  "ipo": False},
    {"code": "6963", "name": "ローム",                    "sub": "パワー半導体", "themes": ["pow", "car"], "ipo": False},
    {"code": "6594", "name": "ニデック",                  "sub": "パワー半導体", "themes": ["car", "pow"], "ipo": False},
]

THEME_LABELS = {"ai": "生成AI", "car": "車載", "pow": "パワー半導体"}
SUBCATEGORIES = ["製造装置", "材料", "設計", "検査", "パワー半導体", "後工程"]

def by_code(code: str):
    return next((s for s in UNIVERSE if s["code"] == code), None)
