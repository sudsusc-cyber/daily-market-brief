# PLAN.md — 每日"市场+你"自动晨报项目

> **本文档是写给 Claude Code(以下简称 CC)执行的项目规格说明书。**
> 用户(开源)只在 GitHub Secrets 配置、API 账号注册、里程碑验收节点参与。
> CC 在每个里程碑完成后**必须停下来**,等待用户验收通过后再进入下一个里程碑。

---

## 0. 文档使用说明(给 CC 看)

### 你的执行模式

- 本项目采用**分里程碑(Milestone)交付模式**,共 6 个里程碑(M1-M6)
- 每完成一个里程碑,**停止工作**,向用户报告完成情况并等待验收
- 用户验收通过后才能进入下一里程碑
- 任何一个里程碑遇到方案级别的疑问(数据源不通、技术选型需调整),**先停下来问用户**,不要自作主张改方案

### 你不可以做的事

- ✗ 不要重新加入已被明确放弃的模块(见第 11 节)
- ✗ 不要替用户注册任何账号(见第 6 节,这是用户责任)
- ✗ 不要把任何 API Key、密码硬编码进代码,一律走 GitHub Secrets
- ✗ 不要在没有用户允许的情况下推送代码到 main 分支或触发生产部署

### 你必须做的事

- ✓ 每个 commit 写清楚 message,中英文皆可
- ✓ 关键决策记录到 `docs/decisions/` 目录(轻量 ADR 格式)
- ✓ 每个里程碑完成时,更新 `PROGRESS.md` 记录状态
- ✓ 代码注释用中文为主,变量名/函数名用英文
- ✓ Python 3.11+,完整 type hints,通过 ruff/black

---

## 1. 项目目标与背景

### 一句话目标

每天早上 8 点前自动发送一封精美 HTML 邮件到 QQ 邮箱,内容覆盖用户跟踪的 12 家公司昨日动态、宏观市场重大新闻、关键人物发言、市场情绪温度计,以辅助每日投资决策。

### 用户背景(供 CC 设定 LLM prompt 时参考)

- **身份**:开源,中国财务岗位从业者,业余价值投资者
- **投资框架**:段永平 + Buffett + Munger + Nalanda Capital 框架,长期持有,反对短期交易
- **估值方法**:两列法(Column 1 净金融资产 + Column 2 Owner Earnings × 倍数)
- **建仓规则**:
  - 120 周均线触发 → 启动 DCA(定投)
  - 200 周均线触发 → 启动 lump-sum(一次性建仓)
  - 200 周线买入的部分**永不无条件卖出**
  - 无信号时持有 BOXX 作为现金等价物
- **核心信念**:本分(Duan Yongping)、能力圈、安全边际、好生意优先于好价格
- **沟通偏好**:简体中文,平实自然,不要 AI 腔(避免"亲""哦""赋能""抓手"等)

### 为什么需要这个项目

用户希望每天早上喝咖啡的 5 分钟内,完成对持仓公司动态、宏观环境、市场情绪的快速扫描,作为当日投资思考的起点,而不是被动响应市场新闻。

---

## 2. 最终交付物

### 用户视角

每天早上 8:00 前,QQ 邮箱收到一封标题为 `每日晨报 · YYYY年MM月DD日` 的 HTML 邮件,内容分为 5 个区块(详见第 4 节模块规格)。

### 邮件视觉方向(创意 brief)

**这不是一份 dashboard,而是一封从信息时代写给一个长期投资者的早间备忘。**

- **基调**:Berkshire Hathaway 致股东信 × Financial Times 周末版 × Howard Marks Memo
- **不要**:花哨配色、emoji、过多图标、卡片堆叠、"现代 SaaS 看板"风
- **要**:克制、典雅、阅读感强、信息层级清晰

**具体设计参数**:

| 维度 | 规范 |
|---|---|
| 整体风格 | Editorial / 杂志版式 / 严肃刊物 |
| 主背景 | 米色或象牙白(`#F8F5EE` 或 `#FAFAF7`),不要纯白 |
| 主文字 | 深墨色(`#1A1A1A` 或 `#2B2B2B`),不要纯黑 |
| 次要文字 | 中灰(`#6B6B6B`) |
| 强调色 | 单一深色,如 oxblood (`#7A1F2B`)、deep navy (`#1F3A5F`)、muted gold (`#A88433`),**全文只用一种**,且只在关键数字、栏目标题处出现 |
| 涨跌色 | 涨用沉稳的森林绿(`#2D5F3F`),跌用克制的暗红(`#8B2A2A`),**避免鲜亮的红绿** |
| 字体 | 标题用衬线显示字体(如 `Newsreader`、`Source Serif Pro`、`Noto Serif SC`);正文用衬线读体(如 `Charter`、`Georgia`、`思源宋体`)。**禁用 Inter / Roboto / Arial / Helvetica** |
| 中英混排 | 必须用 `font-family` fallback 链处理,英文用衬线西文,中文用思源宋体或类似 |
| 分隔 | 使用极细横线(0.5px-1px)分割区块,不要厚边框、阴影、圆角卡片 |
| 留白 | 慷慨,栏目之间至少 32px,段落之间 16px |
| emoji / 图标 | **零容忍**,一个都不要 |
| 数字呈现 | 数字单独占行或单独栏,字号大于正文,字重稍重,但不要 800+ 极粗 |

**参考意象**:打开邮件像翻开一份老牌财经周刊的内页,不是登录一个 App。

### 邮件样板示意

第 5 节有完整伪示例。

---

## 3. 技术架构

### 整体数据流

```
[GitHub Actions cron · 每天 23:30 UTC = 北京时间次日 7:30]
        ↓
[Python 主程序 main.py]
        ↓
[数据采集层 collectors/]
   ├─ stocks.py        股价 + 均线计算 (yfinance)
   ├─ company_news.py  持仓公司新闻 (Finnhub)
   ├─ macro_news.py    宏观头版 (WSJ/FT/Bloomberg RSS)
   ├─ figures.py       黄仁勋 / 巴菲特发言 (Google News)
   ├─ buffett_13f.py   巴菲特持仓季度变动 (SEC EDGAR)
   └─ sentiment.py     情绪指标 (CNN F&G + VIX + Shiller PE + DXY + AAII)
        ↓
[原始数据 → JSON 中间产物]
        ↓
[LLM 处理层 processors/]   DeepSeek V4-Flash API
   ├─ news_summarizer.py    持仓新闻摘要为段落
   ├─ macro_filter.py       宏观新闻筛选 + 摘要
   ├─ figure_filter.py      判断"是否本人原话"+ 摘要
   └─ sentiment_judge.py    综合情绪一句话结论
        ↓
[模板渲染层 renderer/]
   └─ Jinja2 → HTML (含 inline CSS, table layout)
        ↓
[发送层 sender/]
   └─ smtplib → QQ 邮箱 SMTP
        ↓
[日志 logs/ + 状态 state/]
```

### 技术栈

| 层 | 选择 | 备注 |
|---|---|---|
| 语言 | Python 3.11+ | type hints 完整 |
| 包管理 | `uv` 或 `poetry`(CC 自定) | 锁定依赖 |
| 调度 | GitHub Actions cron | 不用本地、不用 VPS |
| LLM | DeepSeek V4-Flash | 模型 ID `deepseek-v4-flash`,OpenAI 兼容格式 |
| 股价 | `yfinance` | 美股、港股 (`.HK`) 都覆盖 |
| 公司新闻 | Finnhub `/company-news` API | 免费版 60 req/min 够用 |
| 宏观 RSS | `feedparser` | WSJ/FT/Bloomberg 各自 RSS |
| Google News | `gnews` 库 或 RSS feed | 搜 "Jensen Huang" / "Warren Buffett" |
| SEC 13F | `sec-api.io` 免费版 / 直接拉 EDGAR | 季度数据,每季度更新一次 |
| 情绪指标 | CNN F&G(非官方 API)、yfinance(VIX/DXY)、multpl.com(Shiller PE)、AAII 周报 | |
| 模板 | Jinja2 | |
| 邮件 | `smtplib` + `email.mime` | QQ SMTP `smtp.qq.com:465` SSL |
| 配置 | `pydantic-settings` | 环境变量 + `.env`(本地) / GitHub Secrets(生产) |
| 日志 | `structlog` 或 `logging` + JSON formatter | |
| 状态持久化 | 提交到仓库的 `state/` 目录 JSON 文件 | 用于"已发送过的发言去重" |

### 项目目录结构

```
daily-market-brief/
├── PLAN.md                      ← 本文档
├── PROGRESS.md                  ← CC 维护的执行进度
├── README.md                    ← 用户面向的简短说明
├── pyproject.toml
├── .gitignore
├── .env.example                 ← 占位,真实 .env 在本地不入库
│
├── .github/
│   └── workflows/
│       └── daily.yml            ← cron 定时配置
│
├── src/
│   ├── __init__.py
│   ├── main.py                  ← 入口
│   ├── config.py                ← 配置(持仓清单、阈值)
│   ├── settings.py              ← Secrets 读取
│   │
│   ├── collectors/
│   │   ├── __init__.py
│   │   ├── stocks.py
│   │   ├── company_news.py
│   │   ├── macro_news.py
│   │   ├── figures.py
│   │   ├── buffett_13f.py
│   │   └── sentiment.py
│   │
│   ├── processors/
│   │   ├── __init__.py
│   │   ├── llm_client.py        ← DeepSeek 封装
│   │   ├── news_summarizer.py
│   │   ├── macro_filter.py
│   │   ├── figure_filter.py
│   │   └── sentiment_judge.py
│   │
│   ├── renderer/
│   │   ├── __init__.py
│   │   ├── render.py
│   │   └── templates/
│   │       └── email.html.j2
│   │
│   ├── sender/
│   │   ├── __init__.py
│   │   └── smtp_sender.py
│   │
│   └── utils/
│       ├── __init__.py
│       ├── retry.py             ← 通用重试装饰器
│       └── dates.py             ← 交易日 / 节假日判断
│
├── state/
│   ├── pushed_figures.json      ← 已推送过的发言(去重)
│   └── last_run.json            ← 上次运行时间戳
│
├── docs/
│   ├── decisions/               ← ADR
│   │   └── 0001-deepseek-v4-flash.md
│   └── runbook.md               ← 故障排查手册
│
├── tests/
│   ├── test_stocks.py
│   ├── test_renderer.py
│   └── ...
│
└── scripts/
    ├── verify_sources.py        ← M1 验证脚本
    └── send_test_email.py       ← 本地测试发邮件
```

---

## 4. 模块规格

每个模块按统一格式描述:**输入 → 数据源 → 处理逻辑 → 输出 → 降级方案**。

### 模块 1:持仓公司昨日新闻

**目的**:用一段叙述性文字,告诉用户跟踪的 12 家公司昨日有什么值得注意的新闻。

**输入**:持仓清单(见第 10 节)

**数据源**:
- 美股 + ADR(BRK.B、TSM、MCO、AXP、KO、AAPL、NVDA、MSFT、COST、GOOG):**Finnhub `/company-news`**,按 ticker 拉昨日新闻
- 港股(0700.HK 腾讯、9992.HK 泡泡玛特):Finnhub 对港股覆盖有限,**降级到 Google News 中文搜索**(关键词如 "腾讯控股 港股")

**处理逻辑**:
1. 对每只股票拉取过去 24 小时新闻(原始 5-30 条)
2. 按发布时间排序,取前 5-10 条标题 + 摘要
3. 喂给 DeepSeek V4-Flash,prompt 要求:
   - 用第三人称叙述
   - 每家公司 1-2 句话(若无重要新闻可跳过)
   - 重点关注:业绩、重大合作、监管、产品发布、人事变动、资本动作
   - **忽略**:股价波动本身、"分析师上调评级"这类二手信息、KOL 评论
   - 风格参考用户偏好(平实,不要 AI 腔)
4. 合成一段 200-400 字的叙述

**输出**:一段中文文字,作为邮件"昨日动态"区块的内容

**降级方案**:
- Finnhub 失败 → 重试 3 次后跳过该股票,在邮件里标注"数据源暂时不可用"
- 全部失败 → 该区块显示"今日无可用数据,请手动登录 Finnhub 检查"
- LLM 失败 → 降级为列表形式,每条新闻一行原始标题,不做摘要

---

### 模块 2:DCA / lump-sum 信号

**目的**:对每只持仓股票,显示当前价相对 120 周和 200 周均线的位置,触发信号则高亮。

**输入**:持仓清单

**数据源**:`yfinance`,拉每只股票最近 250 周(约 5 年)的周线数据

**处理逻辑**:
1. 计算 120 周 SMA、200 周 SMA
2. 取最新收盘价 `last_close`
3. 计算两个比例:
   - `delta_120 = (last_close - sma_120) / sma_120`
   - `delta_200 = (last_close - sma_200) / sma_200`
4. 信号判断:
   - `last_close <= sma_120` → **DCA 信号**(显眼)
   - `last_close <= sma_200` → **LUMP-SUM 信号**(更显眼)
   - 否则 → 显示距离最近均线百分比

**输出**:一个表格(HTML 邮件中以 `<table>` 实现),每行:

| 标的 | 现价 | 120w | 200w | 信号 |
|---|---|---|---|---|
| NVDA | 142.30 | 156.80 (-9.2%) | 118.40 (+20.2%) | **DCA** |
| MSFT | 432.10 | 410.50 (+5.3%) | 360.20 (+19.9%) | — |

**降级方案**:`yfinance` 失败 → 重试,仍失败则该股票行显示"数据获取失败",其他股票照常显示

---

### 模块 3:关键人物发言 + 巴菲特持仓动向

#### 3a. 黄仁勋发言

**目的**:监控黄仁勋(Jensen Huang)昨日是否有公开发言/采访/演讲,有则摘录关键观点。

**数据源**:Google News 搜索 `"Jensen Huang"`,过去 24 小时

**处理逻辑**:
1. 拉过去 24 小时所有结果(可能 20-100 条)
2. **第一道筛选**(规则):标题或摘要必须包含 "Jensen Huang" 或 "Huang said" / "Huang told" / "Huang announced" 等
3. **第二道筛选**(LLM):喂给 DeepSeek 判断"这是否是他本人的原话/演讲/采访,而非他人转述其旧话或评论他",输出 `is_direct: bool`
4. 保留 `is_direct=True` 的,去重(同一观点多家媒体报道,合并)
5. 摘出 1-3 个关键观点,每个 1-2 句中文

**输出**:若有则展示;若无则该子区块**完全不出现**(不要写"今日无发言")

**去重**:已推送的发言记录到 `state/pushed_figures.json`,key 为 `(person, content_hash)`,7 天内不重复推送

#### 3b. 巴菲特发言 / Berkshire 动态

**数据源**:Google News 搜 `"Warren Buffett"` + 监控 `berkshirehathaway.com/news.html` RSS(若有)

**触发条件**(频率低):
- Berkshire 季报发布
- 致股东信发布(每年 2 月底)
- 股东大会(每年 5 月初)
- CNBC 等正式采访
- 重大资本动作公告

**处理逻辑**同 3a,但门槛更高(巴菲特噪音更多)

**输出**:有则展示,无则不出现

#### 3c. 巴菲特持仓动向(13F)

**数据源**:SEC EDGAR(免费),CIK 1067983(Berkshire Hathaway Inc)

**触发条件**:每个季度 13F 披露后 1-2 周内(约 2 月、5 月、8 月、11 月中旬)

**处理逻辑**:
1. 检查最新 13F 提交时间,与本地 `state/last_13f.json` 对比
2. 若有新提交,对比上一份,计算:
   - 新增仓位
   - 加仓
   - 减仓
   - 清仓
3. 同时拉伯克希尔最新季报中的现金/类现金占比

**输出**:仅在新 13F 发布后的当日和接下来 7 天内出现,每天展示一次摘要;之后该子区块消失

**所有 3a/3b/3c 都触发不到时**:整个"关键人物"区块**不出现**,而不是显示空区块

---

### 模块 4:宏观重大新闻

**目的**:WSJ / FT / Bloomberg **头版级别**的当日重大新闻,合成一段叙述。

**数据源**:
- Wall Street Journal RSS(`https://feeds.content.dowjones.io/public/rss/RSSWorldNews`)
- Financial Times RSS(`https://www.ft.com/?format=rss`)
- Bloomberg RSS(具体源 CC 验证时确定)

**处理逻辑**:
1. 拉过去 24 小时各源的头版/Top Stories 板块
2. **合并去重**(同一新闻多家报道,以 LLM 判断为同一主题)
3. 喂给 DeepSeek 筛选:
   - prompt 要求:"从以下新闻中筛选出 3-5 条**真正影响全球市场或重大经济**的头版级新闻。仅包括:央行政策、重大地缘政治、影响万亿级资产的监管变动、宏观数据(非农、CPI、GDP)、系统性风险事件。**排除**:个股新闻、行业评论、人物花边、'分析师认为'类二手观点。"
4. 对筛选后的 3-5 条,生成一段 150-300 字的叙述,而非列表

**输出**:一段叙述性文字

**降级方案**:某 RSS 源失败 → 跳过该源;全部失败 → 区块显示"今日宏观数据获取失败"

---

### 模块 5:情绪温度计

**目的**:用一句结论 + 简短论据,告诉用户当前市场情绪冷热。

**数据源**(方案 B 减去北向资金):

| 指标 | 数据源 | 说明 |
|---|---|---|
| CNN Fear & Greed | `https://production.dataviz.cnn.io/index/fearandgreed/graphdata` | 非官方但稳定的 JSON 端点 |
| VIX | `yfinance("^VIX")` | 美股恐慌指数 |
| Shiller PE | `https://www.multpl.com/shiller-pe` 抓取 | 估值压力 |
| 两融余额 | Tushare 或东财 | A 股杠杆 |
| 恒指 RSI | `yfinance("^HSI")` 计算 14 日 RSI | 港股技术面 |
| DXY | `yfinance("DX-Y.NYB")` | 美元指数 |
| 高收益债利差 | FRED `BAMLH0A0HYM2` | 信用风险偏好 |

**处理逻辑**:
1. 采集所有指标当日值 + 一周前值
2. 喂给 DeepSeek,prompt 要求:
   - 输出**一句结论**:"今日情绪:偏冷 / 中性 / 偏热 / 极度恐慌 / 极度贪婪"
   - 后跟 **2-3 句论据**,引用最关键的 2-3 个指标数字
   - **联系用户的投资框架**:若偏冷,提示"DCA 信号增强";若极度贪婪,提示"建议暂缓加仓"
3. 风格保持平实,不夸张

**输出示例**:

> **今日情绪:偏热**
>
> CNN Fear & Greed 72 接近贪婪极值,VIX 14.3 处于年内低位,Shiller PE 32 位于历史 90 分位。美股估值与情绪共振,DCA 信号触发概率较低,建议保持现金仓位耐心。

**降级方案**:某个指标取不到 → 跳过该指标,LLM prompt 中只用拿到的;全部失败 → 区块显示"情绪指标获取失败"

---

## 5. 邮件样板示意(伪示例)

```
┌─────────────────────────────────────────────────┐
│                                                 │
│           每日晨报 · 二〇二六年五月一日         │
│                  ── 开 源 ──                    │
│                                                 │
├─────────────────────────────────────────────────┤
│                                                 │
│  情 绪 温 度 计                                 │
│                                                 │
│  今 日 情 绪  :  偏 热                          │
│                                                 │
│  CNN Fear & Greed 72 接近贪婪极值,VIX          │
│  14.3 处于年内低位,Shiller PE 32 位于历史      │
│  90 分位。DCA 信号触发概率较低,建议保持        │
│  耐心。                                         │
│                                                 │
│  ───────────────────────────────────────       │
│                                                 │
│  持 仓 信 号                                    │
│                                                 │
│  标的       现价      120w        200w     信号 │
│  NVDA      142.30   156.80      118.40   DCA   │
│  MSFT      432.10   410.50      360.20    —    │
│  AAPL      ...                                  │
│  ...                                            │
│                                                 │
│  ───────────────────────────────────────       │
│                                                 │
│  昨 日 动 态                                    │
│                                                 │
│  英伟达昨日召开开发者大会,公布 Rubin Ultra    │
│  路线图。微软与 OpenAI 续签算力协议至          │
│  2030 年。腾讯三季报披露广告业务同比增         │
│  长 18%,游戏业务受版号利好出现回暖。台积       │
│  电法说会上调全年资本开支指引至 420 亿美       │
│  元。其他持仓无重要新闻。                       │
│                                                 │
│  ───────────────────────────────────────       │
│                                                 │
│  关 键 发 言       (本区块仅在有内容时出现)    │
│                                                 │
│  黄仁勋(GTC 主题演讲):"接下来五年的         │
│  AI infrastructure 投入仍处于早期阶段,        │
│  推理需求增长远超预期。"                        │
│                                                 │
│  ───────────────────────────────────────       │
│                                                 │
│  宏 观 视 野                                    │
│                                                 │
│  美联储 4 月会议纪要显示官员对降息时点分        │
│  歧加大。中国 4 月 PMI 49.8 重回收缩区          │
│  间,出口压力显现。中东油价因伊朗局势单         │
│  日跳涨 4%。日央行维持利率不变但暗示年         │
│  内退出 ETF 购买。                              │
│                                                 │
├─────────────────────────────────────────────────┤
│                                                 │
│        本邮件由开源的私人晨报系统发送          │
│              数据截至 04-30 23:00 UTC          │
│                                                 │
└─────────────────────────────────────────────────┘
```

(实际渲染为衬线字体、米色背景、深墨文字、单一 oxblood 强调色的精美 HTML)

---

## 6. 用户先做(必须人工完成)

**注意:此清单由用户(开源)在 M1 之前完成。CC 不要尝试代劳。**

### 账号注册

- [ ] **GitHub 账号**(若无)→ 创建私有仓库 `daily-market-brief`
- [ ] **DeepSeek 平台**(`platform.deepseek.com`)→ 注册 → 充值至少 ¥10 测试
- [ ] **Finnhub 账号**(`finnhub.io`)→ 注册免费版,拿 API Key
- [ ] **FRED 账号**(`fred.stlouisfed.org`)→ 注册免费版,拿 API Key(用于宏观指标)
- [ ] **QQ 邮箱 SMTP**:
  - 登录 QQ 邮箱网页版 → 设置 → 账户
  - 开启 IMAP/SMTP 服务
  - 生成"授权码"(16 位字符串)
  - **这个授权码是邮件发送密码,不是 QQ 登录密码**

### GitHub Secrets 配置

仓库 → Settings → Secrets and variables → Actions → New repository secret,逐个添加:

| Secret 名 | 值来源 |
|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek 平台生成 |
| `FINNHUB_API_KEY` | Finnhub 注册后提供 |
| `FRED_API_KEY` | FRED 注册后提供 |
| `QQ_EMAIL_ADDRESS` | 你的完整 QQ 邮箱地址 |
| `QQ_EMAIL_AUTH_CODE` | 上面生成的 16 位授权码 |
| `EMAIL_RECIPIENT` | 接收邮箱(可与发送邮箱相同) |

### 本地开发(可选)

若用户希望在本地调试:

- [ ] 安装 Python 3.11+
- [ ] 克隆仓库
- [ ] 复制 `.env.example` → `.env`,填入相同的值
- [ ] `uv sync` 或 `pip install -e .`
- [ ] `python scripts/send_test_email.py` 测试

---

## 7. 里程碑分解

### M1 — 可行性验证(CC 主导,用户验收)

**目标**:在写任何业务代码之前,验证所有数据源都能拿到数据。

**CC 任务**:
1. 创建项目骨架(目录结构、`pyproject.toml`、`.gitignore`)
2. 在 `scripts/verify_sources.py` 中,逐个写**最小验证脚本**:
   - 用 `yfinance` 拉 NVDA 5 年周线,打印最近 3 周数据
   - 用 Finnhub API 拉 NVDA 昨日新闻,打印标题列表
   - 用 Google News 搜 "Jensen Huang",打印过去 24 小时结果数
   - 拉 CNN Fear & Greed,打印当前数值
   - 拉 VIX、DXY、^HSI,打印最新值
   - 抓 Shiller PE 网页,打印数值
   - 拉 WSJ / FT / Bloomberg RSS,打印过去 24 小时条目数
   - 拉 SEC EDGAR Berkshire 13F 列表
   - 调用 DeepSeek V4-Flash API,发一句 hello,打印响应
   - 用 SMTP 发一封测试邮件到 QQ 邮箱
3. 生成一份 `docs/feasibility-report.md`,每个源标记 ✅/⚠️/❌ + 备注
4. 对 ⚠️ 和 ❌ 的源,**给出降级方案建议**,等用户决策

**验收标准**:
- 用户收到测试邮件
- `feasibility-report.md` 显示至少 8/10 数据源 ✅
- 任何 ❌ 的源,用户已经决定"降级"或"放弃该模块"

**禁止做的**:在 M1 阶段,不要开始写业务代码、不要做模板设计、不要做 LLM prompt 调优。

---

### M2 — MVP(端到端最小流程)

**目标**:跑通"采集 → 渲染 → 发送"完整链路,但只实现**模块 2(信号)**这一个。

**CC 任务**:
1. 实现 `collectors/stocks.py`,完成 12 只股票的均线计算
2. 实现 `renderer/render.py` + 简陋版 `email.html.j2`(只显示一个表格)
3. 实现 `sender/smtp_sender.py`
4. `main.py` 串起来
5. 实现 `config.py` 读取 Secrets
6. 本地能跑通,发出第一封"丑陋但功能正确"的邮件
7. 写第一份 ADR `docs/decisions/0001-mvp-scope.md`

**验收标准**:
- 用户在 QQ 邮箱收到一封邮件,标题正确,12 只股票表格正确
- 表格里的现价、均线、信号判断**对一遍**(可选 1-2 只手动核对)
- 暂时不要求好看

---

### M3 — 数据采集层完善

**目标**:把所有 5 个模块的**原始数据采集**全部实现,但**不接 LLM**,直接把原始数据 dump 到邮件里。

**CC 任务**:
1. 实现剩余的 collectors:`company_news`、`macro_news`、`figures`、`buffett_13f`、`sentiment`
2. 实现 `utils/retry.py` 通用重试装饰器(指数退避,最多 3 次)
3. 修改邮件模板,加入新区块,但内容**直接展示原始数据**(新闻标题列表、指标数字列表)
4. 实现状态持久化:`state/pushed_figures.json` 去重

**验收标准**:
- 邮件包含 5 个区块的原始数据
- 每个数据源都有失败重试和降级
- 用户人工审阅一封"原始数据邮件",确认每块数据**真实可信**(没有幻觉、没有错位)

---

### M4 — LLM 处理层

**目标**:接入 DeepSeek V4-Flash,把原始数据加工成自然语言段落。

**CC 任务**:
1. 实现 `processors/llm_client.py`(OpenAI 兼容 client,base_url 指向 DeepSeek)
2. 实现 4 个 processor,每个有详细 prompt:
   - `news_summarizer.py`:持仓新闻 → 一段话
   - `macro_filter.py`:宏观新闻 → 筛选 + 一段话
   - `figure_filter.py`:发言判断 + 摘要
   - `sentiment_judge.py`:情绪综合判断
3. **每个 prompt 都要包含用户的投资框架上下文**(见第 10 节)
4. 实现 token 预算监控(每次调用记录 input/output tokens 到日志)
5. LLM 失败降级:回退到原始数据展示

**验收标准**:
- 邮件内容从"原始列表"变成"自然段落"
- 用户连续看 3 天的邮件,主观感受是"像专业财经摘要,不是 AI 腔"
- 每天 LLM 成本 ≤ ¥0.5(从日志统计)

---

### M5 — HTML 精美化 + 兼容性

**目标**:把邮件从"功能正确但简陋"升级到第 2 节描述的设计标准。

**CC 任务**:
1. 重写 `email.html.j2`,严格遵守第 2 节"邮件视觉方向"
2. 使用 table-based layout(邮件 HTML 必须),inline CSS
3. 字体 fallback 链处理中英混排
4. **测试 QQ 邮箱、Gmail、Outlook 网页版**渲染效果(CC 截图给用户看)
5. 加入"打印友好"样式(用户可能想存档)
6. 处理夜间模式适配(深色模式下不要一团黑)

**禁止**:
- 用 Inter / Roboto / Arial
- 用 emoji 或 icon
- 用 `<style>` 块(部分邮箱不支持)
- 用 flexbox / grid(邮箱兼容性差,改用 table)
- 用 JavaScript

**验收标准**:
- 用户在 QQ 邮箱中看到的视觉效果与第 5 节伪示例的"严肃刊物感"一致
- 同时在 Gmail 网页版打开,布局不崩
- 用户主观评价:"这个邮件我愿意每天读"

---

### M6 — 部署上线 + 稳定性

**目标**:GitHub Actions cron 接管,跑一周稳定。

**CC 任务**:
1. 写 `.github/workflows/daily.yml`:
   - cron: `30 23 * * 0-4`(周一到周五凌晨,北京时间次日 7:30)
   - 周末跳过(用户不需要)
   - 失败时自动 retry 1 次
   - 失败时发告警邮件到用户(用 GH Action 的 mail action)
2. 实现节假日跳过逻辑(美股、港股、A 股各自的节假日不发也可,或发"今日休市"提示)
3. 加强日志(JSON 格式,包含每个模块耗时、token 用量、失败原因)
4. 写 `docs/runbook.md`:常见故障如何排查
5. 写 `README.md`:用户使用说明

**验收标准**:
- 连续 5 个工作日,用户在 8:00 前收到邮件
- 任何一天出现失败,用户收到告警邮件 + 仓库 Actions 日志可查
- 用户能根据 runbook 自己处理常见问题(如 API Key 过期)

---

### M7(可选)— 持续优化

完成 M6 之后,项目正式进入运行期。后续可能的优化任务,**不要在 M6 之前做**:

- 加入更多持仓股票
- 调整 prompt 提升摘要质量
- 加入"周报"(周末汇总当周变化)
- 加入个人笔记同步(若用户改主意了)
- 移植到自托管(若 GH Actions 出限制)

---

## 8. 工程规范

### 代码风格

- Python 3.11+,所有函数必须有 type hints
- 用 `ruff` 做 linting,`black` 做格式化(line-length 100)
- 函数 docstring 用中文,说明"做什么 + 注意事项"
- 变量/函数/类名一律英文,避免拼音
- 注释中文为主

### 错误处理

- **数据采集层**:每个 collector 用 `@retry(max_attempts=3, backoff="exponential")` 装饰
- 失败时**不要 raise**,而是返回 `None` 或带 `error` 字段的 dict,让上层决定降级
- 上层(`main.py`)汇总所有 collector 状态,任何一个失败都要在最终邮件里有所体现(不要静默)

### 配置 vs Secrets

- **配置**(持仓清单、阈值、URL):写在 `config.py`,提交到仓库
- **Secrets**(API Key、密码):走环境变量,**绝对不要**入库
- `pydantic-settings` 自动校验 Secrets 是否齐全,缺失时启动失败

### 日志规范

```
{
  "ts": "2026-04-30T07:31:23Z",
  "level": "INFO",
  "module": "company_news",
  "ticker": "NVDA",
  "msg": "fetched_news",
  "count": 12,
  "duration_ms": 340
}
```

### 测试

- M2 之前不强求测试
- M3 起,核心计算逻辑(均线、信号判断)必须有 unit test
- 数据源 collector 不写 unit test(外部依赖,易脆),改为在 `verify_sources.py` 做集成验证

### 提交规范

- commit message 格式:`<type>(<scope>): <subject>`
- type:`feat` / `fix` / `docs` / `refactor` / `test` / `chore`
- 例:`feat(stocks): add 200w SMA calculation`

---

## 9. 边界情况清单

| 情况 | 处理 |
|---|---|
| 某 API 限流 / 故障 | 重试 3 次 → 失败则跳过该数据,邮件中标注 |
| LLM 返回为空或异常 | 回退到原始数据列表 |
| 邮件发送失败 | 重试 3 次 → 通过 GitHub Actions 失败通知用户 |
| 美股节假日(如圣诞) | 跳过当日,不发邮件,或发"今日美股休市,仅含港股动态"版本 |
| 港股节假日 | 不发"昨日动态"中的港股部分 |
| 某只股票退市 / 改代码 | `config.py` 中持仓清单出错 → 启动时校验 + 告警 |
| 13F 季报刚发布 | 状态文件记录,7 天后停止展示 |
| 同一发言被多家媒体报道 | LLM 判断同主题去重 |
| 用户 GitHub Secrets 误删 | 启动时校验,缺失则发告警邮件,不发当日早报 |
| GitHub Actions 免费额度耗尽 | 用户检查仓库 Actions 计费(实际不会发生,本项目耗时极少) |
| 时区错误导致"昨日"算错 | 全程用 UTC 计算,展示时转 Asia/Shanghai |
| 用户改持仓 | 编辑 `config.py` 中清单 → 推送 → 次日生效 |

---

## 10. 投资上下文(LLM Prompt 的 system prompt 基底)

**所有 LLM 调用必须以以下内容作为 system prompt 的开头**(可根据具体任务在后面追加):

```
你是为开源(一位中国财务从业者、业余价值投资者)服务的私人投资信息助手。
开源的投资框架是:

- 段永平、Buffett、Munger、Nalanda Capital 的长期价值投资体系
- 关注"本分"(企业是否做对的事、是否做难而正确的事)
- 估值方法是"两列法":Column 1 净金融资产 + Column 2 Owner Earnings × 合理倍数
- 建仓规则:
    - 股价跌破 120 周均线 → 启动 DCA(分批定投)
    - 股价跌破 200 周均线 → 启动 lump-sum(一次性建仓)
    - 200 周线买入的部分**永不无条件卖出**
    - 无信号时持有 BOXX 作为现金等价物
- 关注的信号是:企业基本面变化、长期竞争力、管理层资本配置能力
- **不关注**:短期股价波动、技术指标(除均线外)、分析师评级、KOL 看法

开源的当前持仓清单:
- 美股:MSFT, COST, AAPL, NVDA, TSM, MCO, GOOG, BRK.B, KO, AXP
- 港股:0700.HK(腾讯)、9992.HK(泡泡玛特)

你的输出语言:简体中文,平实自然,**避免**:
- AI 腔(如"亲""哦""赋能""抓手""一站式""全方位")
- 营销话术(如"震撼""炸裂""不容错过")
- 过度修饰(如"非常非常""极其重要")
- 中英混杂(除非英文术语必要,如 "DCA")

直接、克制、有信息密度。把开源当作一个有 5 年投资经验的人来沟通,不要解释他已经懂的常识。
```

各模块的 prompt 在此基础上追加具体任务说明。

---

## 11. 已明确放弃的(不要重新加回来)

CC 看到这一节,如果觉得"加回来这个会更好",**先停下来问用户**,不要自己决定。

- ❌ **段永平监控**:雪球反爬严,降级方案复杂度过高,用户已决定砍掉
- ❌ **雪球评论情绪分析**:信号噪音比太高,改为结构化情绪指标
- ❌ **个人笔记复盘**:用户已决定不做
- ❌ **北向资金**:从情绪温度计中移除
- ❌ **每日推送 Bark / 微信通知**:这是另一个项目(打卡推送),已被用户主动放弃,不要混进本项目

---

## 12. 维护手册(M6 完成后写到 docs/runbook.md)

### 怎么改持仓清单

编辑 `src/config.py`,修改 `HOLDINGS` 列表 → `git commit` → `git push` → 次日生效。

### 怎么调 prompt

每个 processor 的 prompt 在对应文件顶部,作为常量。修改后 push 即可生效。

### 怎么换 LLM 模型

`src/processors/llm_client.py` 中改 `MODEL` 常量。如果换了非 DeepSeek 模型,可能需要改 base_url 和兼容性。

### 怎么排错

- 第一查:GitHub 仓库 Actions 页面的最新一次 run 日志
- 第二查:邮件标题是否含 `[ERROR]` 前缀
- 第三查:`docs/runbook.md` 故障对照表

### 怎么暂停

仓库 Settings → Actions → Disable workflows。

### 怎么恢复

同上,重新 enable。

---

## 13. CC 启动指引

### 第一次执行本 plan 时

1. 阅读本文档全文
2. 创建 `PROGRESS.md`,初始化 6 个里程碑状态
3. **进入 M1**,生成 `verify_sources.py` 和验证报告
4. **停下来**,等用户验收 M1
5. 用户说 "M1 通过,进 M2",再开始 M2

### 关键自我提问(每次开始一个 M 之前)

- 这个 M 的范围是什么?哪些是 M+1 的事,我别提前做?
- 这个 M 的验收标准是什么?用户怎么判断我做完了?
- 我是否需要用户先做点什么(比如新建 Secret)?

### 关键自我提问(每次完成一个 M 之后)

- `PROGRESS.md` 更新了吗?
- 有没有产生需要记录的决策?写 ADR 了吗?
- 有没有写下任何"我假设了什么"?

---

## 14. 项目结束的判断

本项目不是"做完就走"的一次性工程,而是一个长期运行的个人服务。

**项目"成功"的真实定义**(用户视角):

- 三个月后,用户每天还在看这封邮件
- 用户至少有过 1 次"因为读了邮件而做出/避免了某个投资决策"
- 不需要用户每周维护

**若 CC 在某个里程碑发现以上目标受到威胁**(如用户反馈"我已经不看了"、"质量不行"、"成本太高"),立刻停下来,与用户讨论是否调整 / 暂停 / 终止项目,而不是继续推进。

---

## 附录 A:持仓清单(权威,以此为准)

| Ticker | 名称 | 市场 | 数据源备注 |
|---|---|---|---|
| MSFT | Microsoft | NASDAQ | yfinance + Finnhub |
| COST | Costco Wholesale | NASDAQ | yfinance + Finnhub |
| AAPL | Apple | NASDAQ | yfinance + Finnhub |
| NVDA | NVIDIA | NASDAQ | yfinance + Finnhub |
| TSM | Taiwan Semiconductor (ADR) | NYSE | yfinance + Finnhub |
| MCO | Moody's | NYSE | yfinance + Finnhub |
| GOOG | Alphabet Class C | NASDAQ | yfinance + Finnhub |
| BRK.B | Berkshire Hathaway B | NYSE | yfinance + Finnhub |
| KO | The Coca-Cola Company | NYSE | yfinance + Finnhub |
| AXP | American Express | NYSE | yfinance + Finnhub |
| 0700.HK | 腾讯控股 | HKEX | yfinance,Google News 中文 |
| 9992.HK | 泡泡玛特 | HKEX | yfinance,Google News 中文 |

---

## 附录 B:常用链接索引

- DeepSeek API 文档:`https://api-docs.deepseek.com`
- Finnhub 文档:`https://finnhub.io/docs/api`
- yfinance:`https://github.com/ranaroussi/yfinance`
- CNN F&G 端点:`https://production.dataviz.cnn.io/index/fearandgreed/graphdata`
- FRED:`https://fred.stlouisfed.org`
- SEC EDGAR Berkshire:`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001067983`
- QQ 邮箱 SMTP 设置:`https://service.mail.qq.com/detail/0/427`

---

**文档版本**:v1.0
**编写日期**:2026-04-30
**编写者**:Claude (与开源协同设计)
**下一步**:用户创建 GitHub 仓库,push 此文档,完成第 6 节"用户先做"清单,然后启动 Claude Code 执行 M1。
