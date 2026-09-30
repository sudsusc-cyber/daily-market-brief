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
