"""Display names only; adding vocabulary never changes cleanup grammar.

Legacy replay keeps its own frozen vocabulary so later display-name changes do
not retroactively invalidate archived source mappings.
"""

_PUBLISHERS = {
    "wsj": ("WSJ", "wsj.com", "The Wall Street Journal", "华尔街日报"),
    "reuters": ("Reuters", "路透社"),
    "bloomberg": ("Bloomberg", "彭博社"),
    "abc australia": ("ABC News Australia", "ABC News", "澳大利亚广播公司"),
    "financial times": ("Financial Times", "FT", "金融时报"),
}

_LEGAL_NAMES = {
    "QUALCOMM Incorporated": "高通",
    "Qualcomm Inc.": "高通",
    "Apple Inc.": "苹果",
    "Microsoft Corporation": "微软",
    "Alphabet Inc.": "Alphabet",
    "The Coca-Cola Company": "可口可乐",
    "Mastercard Incorporated": "万事达",
}

_FINANCIAL_TERMS = {
    "Federal Reserve": "美联储",
    "Fed": "美联储",
    "Treasuries": "美国国债",
    "Strait of Hormuz": "霍尔木兹海峡",
    "Hormuz": "霍尔木兹海峡",
    "European Union": "欧盟",
    "EU": "欧盟",
    "Germany": "德国",
    "Saudi Arabia": "沙特阿拉伯",
    "Australia": "澳大利亚",
    "Iran": "伊朗",
    "Satya Nadella": "萨提亚·纳德拉",
}

# Shared company labels used by prompts and adjacent listing recognition.
COMPANY_DISPLAY_NAMES: dict[str, str] = {
    "MSFT": "微软",
    "COST": "好市多",
    "AAPL": "苹果",
    "NVDA": "英伟达",
    "TSM": "台积电",
    "MCO": "穆迪",
    "GOOG": "谷歌",
    "BRK.B": "伯克希尔",
    "KO": "可口可乐",
    "AXP": "运通",
    "0700.HK": "腾讯",
    "9992.HK": "泡泡玛特",
    "MA": "万事达",
    "LIN": "林德",
}


# Versioned geographic/institutional vocabulary; product identifiers stay exact.
_LOCALIZED_TERMS_V9 = {
    "Bank of Japan": "日本央行", "BOJ": "日本央行",
    "Bank of England": "英国央行", "BOE": "英国央行",
    "European Central Bank": "欧洲央行", "ECB": "欧洲央行",
    "Eurozone": "欧元区", "euro area": "欧元区",
    "United States": "美国", "United Kingdom": "英国",
    "Arizona": "亚利桑那州", "Texas": "得克萨斯州", "California": "加利福尼亚州",
    "China": "中国", "Japan": "日本", "South Korea": "韩国", "India": "印度",
    "Singapore": "新加坡", "France": "法国", "Italy": "意大利", "Spain": "西班牙",
    "Canada": "加拿大", "Mexico": "墨西哥", "Brazil": "巴西", "Russia": "俄罗斯",
    "Iraq": "伊拉克", "United Arab Emirates": "阿联酋", "Netherlands": "荷兰",
    "San Francisco": "旧金山", "AI Agent": "AI 智能体", "AI agents": "AI 智能体",
}
