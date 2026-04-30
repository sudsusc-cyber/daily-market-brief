# ADR-0002 — M2 MVP 范围与"M2 提前做精美设计"的偏差

- 日期:2026-04-30
- 状态:已采纳
- 影响范围:M2 / M5
- 上一份:ADR-0001(M1 可行性发现)

## 背景

PLAN.md 第 7 节原本规定:
- **M2**:跑通端到端最小流程,只实现"持仓信号"模块,**暂时不要求好看**
- **M5**:HTML 精美化 + 兼容性,严格遵循第 2 节"邮件视觉方向"

用户在 M2 启动后追加指令:邮件设计**直接做精美版**,使用 `anthropic-skills:frontend-design` 辅助。
本 ADR 记录这一范围调整,并锁定 M2 / M5 的新边界。

## 决策

### 1. M2 范围实际交付

- **数据采集**:仅模块 2(持仓信号),`yfinance` + 12 只持仓周线计算 → SMA120 / SMA200 / DCA / LUMP-SUM 判断
- **设计**:**采用 PLAN 第 2 节的精美设计标准**,不做"丑陋占位版"。模板 `src/renderer/templates/email.html.j2` 由 frontend-design skill 一次性生成
- **强调色**:从 oxblood / deep navy / muted gold 三选一,**选定 oxblood `#7A1F2B`**(最贴合 Berkshire 致股东信 / FT Weekend 的"financial gravitas"基调)
- **模板已为 M3 区块预留位**:情绪温度计 / 昨日动态 / 关键发言 / 宏观视野,各自用 `{% if section %}` 包,M2 不传则不渲染
- **Jinja2 filter**:`price`(千分位 + 2 位小数)/ `pct`(带正负号百分比)在 `src/renderer/render.py` 注册

### 2. M5 的新边界

PLAN 原 M5"HTML 精美化"已大量被 M2 提前完成。M5 重新定义为:
- **跨邮件客户端兼容性测试**:QQ 邮箱网页版 / Gmail / Outlook 网页版的实际渲染截图与对比
- **暗色模式适配验证**:确保深色背景下不变成"一团黑"
- **打印友好样式**:用户可能想存档(PLAN 第 7 节 M5 任务 5)
- **设计微调**:基于 M2 → M5 期间累积的反馈(如表格行密度、字号),做小幅修正
- **不再**做整体重设计

### 3. 为什么不在 M2 阶段写测试

- PLAN 第 8 节"测试"明确:M2 之前不强求测试
- M2 的"端到端最小流程"靠 **真实运行 + 用户人工核对** 验证(PLAN 第 7 节 M2 验收点)
- M3 起会对均线/信号判断这类纯计算逻辑加 unit test

## 已遵守的 PLAN 约束(双向核对)

- PLAN 第 0 节"必须做的事"
  - [x] commit message 中文,见 git log
  - [x] 关键决策记录到 `docs/decisions/`(本文件 + ADR-0001)
  - [x] 每里程碑更新 `PROGRESS.md`
  - [x] 注释中文,变量/函数名英文
  - [x] Python 3.11+ 完整 type hints

- PLAN 第 2 节"邮件视觉方向"
  - [x] 米色背景 `#F8F5EE`(选用)
  - [x] 深墨主文字 `#1A1A1A`,次要 `#6B6B6B`
  - [x] 单一强调色 oxblood `#7A1F2B`
  - [x] 涨跌色未启用(M2 数据未触发到需要涨跌色的场景);M3 起的"昨日动态"段落如要嵌入涨跌数字,使用 `#2D5F3F` / `#8B2A2A`
  - [x] 衬线字体(Newsreader / Source Serif Pro / Charter / Cambria / Georgia / Noto Serif SC / Songti SC)
  - [x] 中英混排 fallback 链已配置
  - [x] 极细横线分隔(1px,无厚边框/阴影/圆角卡片)
  - [x] 留白慷慨(栏目间 36-56px,段落间 18px)
  - [x] 无 emoji / 图标
  - [x] 数字单独栏右对齐,字号大于正文,字重 500

- PLAN 第 7 节 M5"禁止"清单
  - [x] 未用 Inter / Roboto / Arial / Helvetica
  - [x] 未用 emoji 或 icon
  - [x] 未用 `<style>` 块,全部 inline CSS
  - [x] 未用 flexbox / grid,全部 table-based layout
  - [x] 未用 JavaScript

- PLAN 第 8 节"工程规范"
  - [x] type hints / 中文 docstring / 英文标识符
  - [x] 数据采集层失败不 raise(stocks.py 的 `_failed()`)
  - [x] 配置 vs Secrets 分离(`config.py` 入库 / `settings.py` 读环境)
  - [x] 日志已结构化(key=value 格式,M3 起加 structlog/JSON formatter)

## 用户反馈(M2 期间已纳入)

- "为 开 源" 署名:删除(用户认为不好看)
- 北京时间日期注入 LLM:已在 ADR-0001 §3 锁,M4 兑现

## 未做(留给后续 M)

- 单元测试(M3 起开始写)
- LLM 调用(M4)
- M3 的 4 个新 collector(company_news / macro_news / figures / buffett_13f / sentiment)
- GitHub Actions cron(M6)
- runbook 文档(M6)
- 节假日跳过(M6)

## 风险点 / 待观察

1. **真实数据全部 NONE / DCA**:M2 跑下来 12 只里只触发 2 个 DCA、0 个 LUMP-SUM。LUMP-SUM 的视觉效果只在 mock 预览(`scripts/preview_email.py`)中验证过。一旦未来真触发了 LUMP-SUM,需要再用真邮件复核排版。
2. **港股精度**:`9992.HK`、`0700.HK` 在 yfinance 上 5 年周线行数 < 200 时会进入 `_failed` 路径(M2 实测 9992.HK 拿到 257 行没问题,但泡泡玛特 IPO 较早期数据可能不齐)。
3. **HK ticker 转换**:目前在 `Holding.yfinance_symbol` 中只处理 `.HK` 后缀;若 M3 加入其他市场(如 `.SS` / `.SZ` A 股)需扩展。
