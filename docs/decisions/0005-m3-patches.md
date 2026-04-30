# ADR-0005 — M2/M3 验收期间的三个补丁

- 日期:2026-04-30
- 状态:已采纳
- 影响范围:M2(已交付)/ M3(已交付)/ M4(LLM 时复用 translate 与相关性过滤)
- 上一份:ADR-0004(M3 collectors)

## 背景

M2 与 M3 验收过程中,用户连续指出三个问题,本 ADR 把对应修复一并记录,以免将来回看 git log 时遗漏决策动机。

## 决策

### 1. M2/M3:TSM logo 移动端不显示 → 方形 + 缩到 128 + strip PNG 元数据

- **现象**:M2 验收时电脑端 QQ 邮箱可见 TSM logo,手机端 QQ 邮箱不显示;M3 切到 FMP 方形版后 QQ 邮箱(电脑 + 手机 + QQ 专属 app)三端都正常,但**微信内嵌 webview 打开邮件时,I 持仓信号区块 TSM 仍不显示;同样的 cid 在 III 昨日动态区块却正常显示**
- **三层根因**(逐步定位,每层修复一个):
  1. **方形约束**:Wikimedia 的 `Tsmc-text.svg` 渲染成 **960×757** 非方形 PNG,部分移动客户端对长宽比偏离 1:1 的 inline 图直接不渲染 → 改用 FMP 250×250
  2. **尺寸约束**:FMP 250×250 比其他 logo(普遍 ≤ 128px)显著大,微信 webview 在密集表格 layout 中渲染怪癖 → 用 `sips -Z 128` 缩到 128×128
  3. **PNG 元数据干扰**(关键):FMP / Wikimedia 给的 PNG 含 `eXIf / gAMA / cHRM` 等装饰 chunks,Google S2 给的 favicon 只有 `IHDR + IDAT`。微信 webview 对带 eXIf 的 PNG 在表格 layout 里有渲染问题 → strip 所有非必要 chunks(只保留 `IHDR / PLTE / tRNS / IDAT / IEND`)
- **决策**:`scripts/fetch_logos.py` 的 fetch_one 在保存后**两步规格化**:
  1. 系统 `sips` 可用时(macOS)把图缩到最长边 ≤ 128
  2. Python 标准库 `struct` 重写 PNG,只保留 5 个最小可解码 chunk(无依赖,Linux GH Actions 也能跑)
- **后续维护**:任何新 override 在 fetch 后用 `file assets/logos/*.png` 验证 dimensions ≤ 128,用以下命令验证 chunks:
  ```python
  data = open('assets/logos/X.png','rb').read()
  # 期望只有 IHDR / IDAT(若调色板图含 PLTE / tRNS)
  ```
- **当前 LOGO_OVERRIDES 列表(全部方形)**:
  - `TSM` → FMP 250×250 → sips 128 → strip(140 bytes 剥离)
  - `COST` → FMP 128×128 → strip(13 bytes)
  - `BRK.B` → FMP 240×240 → sips 128 → strip(16 bytes)

### 2. M3:Reuters Top News RSS 完全不可达 → 替换为 CNBC

- **现象**:邮件 V 区块 Reuters 子区块持续显示"数据获取失败 · SSLError"
- **根因**:`feeds.reuters.com` 自 2020 年起逐步关停,当前 SSL 直接断连
- **决策**:不再尝试修 Reuters,直接换为 **CNBC Top News**(`https://www.cnbc.com/id/100003114/device/rss/rss.html`)
  - 财经向更贴本项目主题
  - 实测当前返回 30 条/24h
- **历史代价**:之前 ADR-0001 §4 / ADR-0004 §3 把 Reuters 列为"备选源"。CNBC 现在升格为正式源,WSJ + FT + Bloomberg + CNBC 全部主源

### 3. M3:邮件全英文标题 → 提前接 LLM 翻译(M4 工作的一小部分)

- **现象**:用户希望 III/IV/V 区块的标题为简体中文
- **背景张力**:PLAN 第 7 节 M3 明确"不接 LLM",M4 才有 `processors/llm_client.py`。但用户要求中文标题客观需要 LLM 翻译
- **决策**:**把 LLM 翻译能力提前到 M3** 作为补丁,但严格限定在"标题翻译"这一个用途,**不做摘要 / 段落叙述**(那是真正的 M4 工作)
- **实施位置**:`src/utils/translate.py`(M4 起会被 `processors/llm_client.py` 整合替代,届时 utils 下这个文件迁过去或删除)
- **关键工程细节**:
  - **批量调用**:一个 prompt 送 30 条标题,用 `▦ N: <标题>` 格式编号,LLM 返回也按此格式分行,显著降低 round-trip 延迟与成本
  - **跳过中文**:`_HAS_CJK` 正则检测,已是中文的不发给 LLM(港股 Google News 中文搜索的标题大多是中文)
  - **失败回退**:LLM 调用失败的批次保留原文,模板照常显示英文,**绝不阻断邮件发送**
  - **prompt 设计**:严格保留编号格式 + 公司/人名保留英文 + 数字/百分号保持原样 + 不输出解释
  - **范围控制**:仅翻译模板实际渲染的"前 5 条"(模板侧 `[:5]` 切片);全量翻译会浪费一半 LLM 调用
- **实测成本**(2026-04-30 跑):
  - 3 批次 × 30/30/8 = 68 条标题 × 翻译
  - prompt_tokens 约 1900,completion_tokens 约 2000,reasoning_tokens 约 500
  - DeepSeek-V4-Flash 折人民币约 ¥0.005-0.01 / 次跑
  - 比 PLAN 第 7 节 M4 给的 "≤¥0.5/天" 预算低两个数量级
- **缓存优化(自然产生)**:第二次跑 prompt_cache_hit_tokens=640,DeepSeek 自动缓存了重复的 system prompt 部分

### 4. M3:Finnhub `/company-news` 同板块新闻噪音过重 → 加相关性过滤

- **现象**:NVDA 下方"昨日动态"前几条多是 Bitcoin / XRP / Meta earnings / Seagate 等,与 NVDA 无关。Finnhub 把同板块、竞品、大盘新闻全部贴 NVDA 标签
- **量化**:NVDA 24h 共 249 条 Finnhub 返回,实测前 15 条只有 1 条真正讨论 NVDA
- **决策**:在 `src/collectors/company_news.py` 中加 **`_RELEVANCE_KEYWORDS`** 字典 + `_is_relevant()` 过滤函数。标题或 summary 必须含 ticker 或公司别名
- **关键词清单(部分,不完全列举)**:
  | Ticker | 关键词 |
  |---|---|
  | MSFT | MSFT, Microsoft, Satya Nadella, Azure, Copilot, Windows, Xbox |
  | NVDA | NVDA, NVIDIA, Nvidia, Jensen Huang, GeForce, CUDA, RTX, Blackwell |
  | TSM | TSM, TSMC, Taiwan Semiconductor, 台积电 |
  | GOOG | GOOG, GOOGL, Google, Alphabet, YouTube, Sundar Pichai, Pixel, Gemini |
  | BRK.B | BRK, Berkshire, Buffett, GEICO, BNSF |
  | KO | Coca-Cola, Coca Cola, Coke, " KO "(单字母 KO 太宽,要求带空格) |
  | …其它见代码 | |
- **港股不过滤**:走 Google News 中文搜索,query 已是公司名,相关性默认高;若过滤反而会把"腾讯控股"的繁体版"騰訊"误删
- **效果**(2026-04-30 跑):整体 567 条 → 199 条(35%)。模板只显示前 5 条,前 5 条相关度大幅提升

## 与 PLAN 的偏差

| 条款 | 偏差 |
|---|---|
| PLAN 第 7 节 M3"不接 LLM" | **打破**。M3 提前接 LLM 一项小用途(标题翻译),由用户 M3 验收期间明确要求触发。M4 启动后会把 `utils/translate.py` 整合进 `processors/llm_client.py`,正式做摘要 / 段落叙述 / 情绪结论 / 是否本人原话判断 |
| PLAN 第 7 节 M5"跨客户端兼容性" | M2 / M3 验收期间发现的 TSM 移动端不显示问题,被在本 ADR §1 提前修复(M5 时不再处理) |

## 不做的(避免延伸出的"另起一摊")

- **不做完整 LLM 客户端封装**:`utils/translate.py` 直接 import `openai.OpenAI` + 写 base_url + model name。不抽象成"通用 LLM client"——那是 M4 起 `processors/llm_client.py` 的职责,届时含 token 预算监控、错误重试、降级逻辑等
- **不做翻译质量评估**:M3 阶段不评估译文好坏,M4 跑两周后由用户主观感受决定是否切换模型
- **不翻译 figures 的 snippet 字段**:只翻 title;snippet 只在 LLM 第二道筛选(M4)时用得到
- **不放宽到全部 raw 新闻**:模板 `[:5]` 切片之外的不翻译,避免无效成本

## 结论

三个补丁全部为"用户验收期间发现的硬伤",修复都是必要的。第三个(LLM 翻译)是范围调整;一与四是 bug 修复;二是数据源切换。本 ADR 把决策与代价交代清楚,后续 M4 启动时不会因看 git log 遗漏背景。
