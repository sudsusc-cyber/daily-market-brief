# ADR-0004 — M3 数据采集层范围、放弃项与降级策略

- 日期:2026-04-30
- 状态:已采纳
- 影响范围:M3(已实施)/ M4(LLM 接入时复核 figures 第二道筛选 / buffett_13f 持仓 diff)
- 上一份:ADR-0003(公司 logo)

## 决策

### 1. M3 实际交付的 5 大模块

| 模块 | 数据源 | M3 输出 | 留给 M4 |
|---|---|---|---|
| 持仓信号(模块 2) | yfinance | 表格(M2 已有,M3 不动) | — |
| 公司新闻(模块 1) | 美股 Finnhub `/company-news`,港股 Google News 中文 RSS | 每只持仓最近北京时间昨日整日的新闻列表(标题 / 时间 / 链接 / 来源) | LLM 摘要为段落叙述 |
| 关键发言(模块 3a/3b) | Google News 英文 RSS,搜 `"Jensen Huang"` / `"Warren Buffett"` 24h 内 | 第一道**规则筛选**(标题 / 摘要含 said / told / announced 等动词)+ 7 天 dedupe 后的候选列表 | 第二道 LLM 筛选(是否本人原话)+ 摘要 |
| Berkshire 13F(模块 3c) | SEC EDGAR atom feed | "是否有新提交"事件 + 最新 accession / filed_at | 13F-HR XML 解析,具体新增 / 加仓 / 减仓 / 清仓 |
| 宏观新闻(模块 4) | WSJ + FT + Bloomberg + Reuters(备选)RSS,过去 24h | 每源头条列表 | LLM 头版级别筛选 + 段落叙述 |
| 情绪温度计(模块 5) | CNN F&G + yfinance(VIX/DXY/^HSI) + multpl(Shiller PE) + FRED(BAMLH0A0HYM2) | 6 个指标小表(当前值 / 一周前 / 变化) | LLM 综合判断 + 一句结论 |

### 2. 已放弃的指标:**两融余额**(A 股杠杆)

PLAN 第 4 节模块 5 的指标候选清单中包含"两融余额",M3 决定**砍掉**。

**理由**:

| 候选数据源 | 障碍 |
|---|---|
| Tushare | 需积分/付费会员,且 token 注册流程繁琐,违背"开箱即用"原则 |
| 东方财富网页 | 反爬严格,需要 selenium/playwright 这种重型依赖 |
| 聚宽 / Choice | 商业 API,与用户"个人项目"成本边界不符 |
| 中证指数公司 / 上交所 | 无稳定免费 JSON 接口 |

**业务侧理由**:

- 用户的核心持仓是美股 + 港股(0700.HK / 9992.HK 是 H 股而非 A 股),A 股杠杆对其投资决策的边际相关性低
- 已有 6 个情绪指标(F&G / VIX / DXY / 恒指 RSI / Shiller PE / HY 利差)覆盖了恐慌、估值、信用、技术多个维度
- 用户在 PLAN 第 11 节主动砍掉过北向资金,两融余额逻辑上同类(A 股局部杠杆/资金流),应一并去除以保持一致性

记入 PLAN 第 11 节"已明确放弃"清单(M3 commit 时同步更新)。

### 3. 降级策略与已知非阻塞失败

| 数据源 | 失败模式 | 处理 |
|---|---|---|
| Reuters Top News RSS(`feeds.reuters.com/reuters/topNews`) | 当前 SSL 直接断连(经过本机代理也不通,Reuters 已基本不维护该 RSS) | 视为"备选源",失败时 bundle.error 填错误信息,模板渲染"数据获取失败"小字。WSJ + FT + Bloomberg 任一可达即满足"宏观视野"区块 |
| Bloomberg RSS proxy | 当前 30 条/24h 正常,但 ADR-0001 §4 已标注非官方代理需观察 | 同 Reuters,失败时降级显示;主源仍是 WSJ + FT |
| Finnhub 港股 | 港股覆盖弱 | 已按 PLAN 降级到 Google News 中文 RSS(`hl=zh-CN&gl=CN`) |
| SEC EDGAR | 405/403/网络抖动 | 重试 3 次,失败则 buffett_13f.error 填原因,模板该子区块不渲染 |
| 单只 yfinance ticker | 临时不可达 | 重试 3 次,失败则该行邮件中显示"数据获取失败" + 原因 |

### 4. 关键的工程基础设施

`src/utils/` 下三个模块,M3 起被各 collector 复用:

| 文件 | 作用 |
|---|---|
| `dates.py` | `now_beijing()` / `now_beijing_human()` / `yesterday_beijing_window()` / `last_24h_window()` / `to_beijing()`。所有"昨日整日"窗口以**北京时间**为准(用户视角),内部时间戳保持 UTC |
| `retry.py` | `@retry(max_attempts=3, base_delay=1.0)` 指数退避装饰器 |
| `fetch_rss.py` | `fetch_rss(url)`:requests + Safari UA → feedparser.parse;不适用于 SEC EDGAR(后者要求联系方式 UA,buffett_13f 单独走 SEC_UA) |

### 5. SEC EDGAR atom 的 accession 解析

EDGAR atom entry 的 id 字段实际格式为:
```
urn:tag:sec.gov,2008:accession-number=0001193125-26-054580
```
M3 在 buffett_13f.py `_fetch_atom` 中显式找 `accession-number=` 后面的 18 字符,而非 split-by-colon(那会得到错误前缀)。

### 6. 测试矩阵

按 PLAN 第 8 节"M3 起核心计算逻辑必须有 unit test":

| 测试文件 | 覆盖 |
|---|---|
| `tests/test_stocks.py` | `_judge_signal()` 各分支(NONE / DCA / LUMP_SUM,临界值) |
| `tests/test_dates.py` | UTC ↔ 北京时间转换、"昨日整日"窗口长度、now_beijing_human 字符串格式 |
| `tests/test_retry.py` | 一次成功 / 第二次成功 / 耗尽抛出 / exception 类型过滤 |
| `tests/test_render_filters.py` | price / pct / metric_num / metric_delta / bj_time 五个 Jinja2 filter |

按 PLAN 第 8 节"数据源 collector 不写 unit test(外部依赖,易脆),改在 verify_sources.py 做集成验证"——5 个新 collector 没写 unit test,集成验证靠 main.py 真实运行 + 用户审阅邮件。

## 与 PLAN 的偏差

| 条款 | 偏差 |
|---|---|
| PLAN 第 4 节模块 5 指标列表 | 砍掉"两融余额"。理由见 §2 |
| PLAN 第 7 节 M3 任务 4 "实现状态持久化:state/pushed_figures.json 去重" | 已实施;同时新增 `state/last_13f.json` 给 buffett_13f 用 |
| PLAN 第 7 节 M3"原始数据 dump 到邮件里" | 已实施;模板中 II/III/IV/V 区块都渲染 list,不调 LLM |

## 后续注意

1. **figures 7 天 dedupe 期间初次跑会清空**:M3 验收前清空过 `state/pushed_figures.json` 让用户能看到完整 IV 区块视觉。**生产部署后(M6)第一次跑** 会推送过去 24h 全部候选;之后每天稳定增量
2. **buffett_13f display window=7 天**:从 latest filing 起 7 天内 is_new=True,之后该子区块自动消失。当前最新 13F 是 2026-02-17,距今 ~70 天,M3 邮件不会显示 13F 子区块——这是正确的
3. **Reuters RSS 后续可能要替换源**:M4 阶段如果想 LLM 做"头版级别"筛选,可考虑加入 Bloomberg 官方 API、CNBC RSS 或 NYT RSS 作为额外源
4. **Bloomberg proxy 非官方风险**:M5 / M6 跑稳定一周后再评估是否换 NYT 等官方源
5. **Finnhub 免费版 60 req/min**:12 只持仓单次 main.py 调用 ~10 次(港股走 Google News 不算),离限额很远
6. **FRED API key**:用户 .env 已填,免费档每日 100k req,完全够用
