# ADR-0011 — "关键发言"区双层质量门槛

**状态**: 已接受
**日期**: 2026-05-01
**里程碑**: M5.10(spec 编号 0008,实际 ADR 顺延为 0011)
**触发**:用户反馈"关键发言"区频繁出现新闻标题、二手转述、不同媒体重复报道,内容空洞,与"关键发言"的定位不符。

## 背景

### 数据源现实
M3 阶段 `src/collectors/figures.py` 通过 Google News + 关键词搜索拉取黄仁勋 / 巴菲特 / 但斌等人物的最近发言,**每天的候选池里大量包含**:
- 财经媒体的纯标题文章(WSJ / Bloomberg / FT 付费墙挡住正文)
- 二手转述、市场评论、KOL 解读
- 不同媒体(第一财经、搜狐、新浪)报道**同一场演讲**的重复条目
- 列表/排行/年度盘点这种"伪发言"

### 上一版做法的问题
M5.7 之前 `figure_filter.py` 仅做 LLM 单层 yes/no 判断 + 去重合并指令,但:
- LLM 在没有原话的情况下被迫用标题填充("黄仁勋:工程师对 AI 至关重要"——这是 MSN 标题不是原话)
- 跨媒体相同条目偶尔漏判,读者看到 2-3 条"同一件事"

## 决策:双层过滤 + 整区块条件渲染

### 第一层:规则预筛(零 token,零延迟)

`_has_quote_marker(item)`:候选必须满足**至少一项**才进入 LLM 层:
- 标题或摘要含中/英双引号包住的 6+ 字符引语(`"…"` / `「…」`)
- 含明确引述标记词:**他说 / 表示 / 认为 / 指出 / said / told / stated / told reporters / in an interview** 等
- 含场合关键词:**演讲中 / 采访中 / 公开信 / 致股东信 / argued / claimed**

不通过 → **直接丢弃**,省 LLM token,且彻底过滤标题党。

### 第二层:LLM 质量判断 + 跨媒体合并

`_TASK_INSTRUCTION` prompt 加三件事:
1. **质量门槛**:必须**真本人原话**且**有实质内容**(数字、明确判断、具体事件);空洞口号(如"AI 是未来")一律 no
2. **跨媒体合并(重中之重)**:不同媒体报道同一场演讲/采访,即使措辞略异也必须合并,prompt 给出具体例子(第一财经 + 搜狐报道但斌发声 → 合并)
3. 提炼 1-2 句中文关键观点

输出行格式 `▦ 1,2,3: yes | <观点>` 表示 1、2、3 三条合并,主索引 1 为代表来源(优先 Reuters/Bloomberg/FT/WSJ > 国内财经 > 门户聚合)。

### Python 端兜底:文本相似度去重

`_similar(a, b, threshold=0.6)` 用 `difflib.SequenceMatcher`(归一化去标点 / 空白后)。LLM 万一漏判,Python 端再做一道。

阈值 0.6 适合中文短句:即便措辞不同但讲同一件事(如"AI 推理需求增长"vs"推理算力供不应求")也会被合并。

### 整区块条件渲染

- **某人物所有候选都不通过**(规则层全砍 / LLM 全 no) → `FigureSummary.items` 空 → main.py 里 `figure_summaries = [f for f in summaries if f.items]` 把整人剔除
- **全员沉默**(`figure_summaries` 空) → 调 `generate_silence_note()` 让 LLM 写一句 12-25 字古典韵味占位语(范围参考"群贤皆默,市自为声",绝不照抄)
- 模板里:章节标题"关键发言 / Voices"+ 顶部分割线 **始终保留**(章节存在感,符合刊物排布);章节内容只展示通过门槛的 voice-item 或那句占位语

不再用 `fallback_raw` 展示原始候选(质量门槛优先于"展示什么")。

### voice-item 视觉降级

避免发言内容像"持仓信号"主章节那样重:
- 人物姓名:14px / 字距 0.2em / 中灰(#6B6B6B);**不用 oxblood、不加粗**
- 引语:17px / italic / 深墨(#1A1A1A)/ line-height 1.8 / 左侧 1px oxblood 竖线 + padding-left 16px(经典杂志引语样式)
- 引语字号略大于正文(16px),显出"是重点"

## 后果

### 预期影响
- "关键发言"区**预期一周仅出现 1-2 次**,大多数日子整区不展示具体引语
- 这是**符合预期的设计**,不是 bug——空显示比假内容更尊重用户
- LLM 调用次数偶尔不变(规则层省的是某些天,LLM 总数稳定),token 总量略降

### 可观测性
日志 keys:
- `figure_filter.rule_pass person=X in=N qualified=M` —— 规则层过滤前后
- `figure_filter.ok person=X qualified=M kept=K` —— LLM 后保留数
- `figure_filter.dropped_silent count=N` —— 整人剔除统计
- `figure_silence.ok chars=N` —— 占位语成功

### 可调参数
- 规则层关键词列表 `_QUOTE_MARKERS_RE` 在 `src/processors/figure_filter.py` 顶部
- 相似度阈值 `_similar(a, b, threshold=0.6)` —— 太严会漏合并、太松会误合并
- 占位语字数 12-25 字硬编码在 `_SILENCE_INSTRUCTION` prompt 里

## 与 spec 的偏差

spec 文档要求 ADR 编号为 `0008`,但本仓库 0008 已被 ADR-0008(M4 验收)占用。本 ADR 顺延为 0011。
