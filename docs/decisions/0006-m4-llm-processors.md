# ADR-0006 — M4 LLM 处理层范围、prompt 设计与降级策略

- 日期:2026-04-30
- 状态:已采纳(代码完成,等待 token 充值后端到端验证)
- 影响范围:M4(全部)/ M5(精美设计沿用,不再调整)/ M6(token 监控集成 GH Actions 日志)
- 上一份:ADR-0005(M2/M3 验收期间 4 个补丁)

## 决策

### 1. M4 实际交付

| 文件 | 角色 |
|---|---|
| `src/processors/llm_client.py` | 共享 DeepSeek client + system prompt 自动注入(投资框架 + 北京时间)+ token 累计统计 + 失败返回 None |
| `src/processors/translator.py` | 从 `utils/translate.py` 迁入,复用 `LLMClient` |
| `src/processors/news_summarizer.py` | 12 家持仓昨日新闻 → 一段 200-400 字叙述 |
| `src/processors/macro_filter.py` | 4 源宏观头条 → 头版级别筛选 + 150-300 字段落 |
| `src/processors/figure_filter.py` | 候选发言 → "是否本人原话"判断 + 关键观点提炼 |
| `src/processors/sentiment_judge.py` | 6 指标 → JSON `{verdict, argument}` |
| `src/main.py` | 6 collector → translator → 4 processor → render |
| `src/renderer/templates/email.html.j2` | II/III/IV/V 区块加"段落优先 / M3 原始数据降级"分支 |

### 2. system prompt 设计(`build_system_prompt`)

按 PLAN 第 10 节 + ADR-0001 §3,system prompt 拼接顺序:

1. **投资框架**(`INVESTMENT_FRAMEWORK` 常量,~600 字符):
   - 段永平 + Buffett + Munger + Nalanda Capital 体系
   - 两列法估值
   - DCA / lump-sum 触发规则
   - 当前持仓清单(12 只)
   - 输出语言约束(简体中文 / 严禁 AI 腔 / 禁中英混杂 / 直接克制)
2. **北京时间**(每次调用都重新计算):`当前时间为北京时间 YYYY 年 MM 月 DD 日(星期 X)HH:MM`,接 `涉及"昨日 / 今日 / 本周"的判断以此为准`
3. **任务相关补充**(各 processor 自带):

prompt 顺序很重要 — 框架在前作为基底,北京时间紧跟约束认知,任务 instruction 最后。每个 processor 调用 `client.chat(user_prompt, task_extra=...)`,user_prompt 是数据,task_extra 是任务说明。

### 3. 4 个 processor 的输出契约

| processor | 输出 | 失败回退 |
|---|---|---|
| `news_summarizer` | `str` 一段 200-400 字 | 返回 None,模板降级到 M3 ticker × 5 新闻列表 |
| `macro_filter` | `str` 一段 150-300 字 | 返回 None,模板降级到 M3 4 源 × 5 标题列表 |
| `figure_filter` | `list[FigureSummary]`(每人含 list[FigureKeyPoint]) | LLM 全 no 时 fallback_raw 保留候选;调用失败时 fallback_raw + error |
| `sentiment_judge` | `dict {verdict, argument}` | 返回 None,模板降级到 M3 6 指标小表(小表无论如何始终渲染,作为依据) |

**失败的形式**:每个 processor 的 `summarize/judge/filter_*` 函数捕获所有异常,返回 None / 带 error 的 dataclass。**不抛异常**,`main.py` 不需要 try-except。

### 4. token 预算监控

- `LLMClient.cumulative` 累积全过程 input/output/reasoning/cache_hit tokens
- `LLMClient.estimate_cost_cny()` 按当前 DeepSeek-V4-Flash 价格(2026-04 档)估算:
  - 标准输入(cache miss):¥0.5 / 百万 token
  - 缓存命中输入:¥0.05 / 百万 token
  - 输出(含 reasoning):¥4.0 / 百万 token
- `main.py` 在所有 processor 跑完后输出 `llm.summary` 日志 + 邮件标题前不计入(成本估算仅作 telemetry,不影响发件)
- 估算公式若失准,刷新 `estimate_cost_cny()` 函数与本 ADR §4

**预估每天调用一次的总成本(基于 M3 实测)**:
- translator(60-90 条标题):~5000 input + 2000 output tokens
- news_summarizer:~1500 input + 700 output
- macro_filter:~600 input + 500 output
- figure_filter:~800 input + 500 output(2 人)
- sentiment_judge:~600 input + 300 output
- **合计**:~8500 input + 4000 output(reasoning ~1000)
- **估算**:¥0.025(输入)+ ¥0.020(输出+reasoning)= **~¥0.05/天**
- 远低于 PLAN 第 7 节 M4 给的 ¥0.5/天 上限(预算可用 10 倍)

### 5. 模板分支策略

每个 M4 加工产物都有"None 时模板自动降级"的逻辑:

```jinja
{% if company_news_paragraph %}
  <段落渲染>
{% else %}
  <M3 原始 ticker × 新闻列表渲染>
{% endif %}
```

这意味着:
- 用户充值前(token 用完)如果意外跑了 main,LLM 失败 → 邮件回到 M3 形态,**不会发出半残邮件**
- M5 / M6 阶段任何 LLM 抖动也走同一降级路径

### 6. utils/translate.py 移到 processors/(轻量重构)

ADR-0005 §3 锁定:M3 阶段 `utils/translate.py` 是临时补丁,M4 时整合进 processors/。
现在已迁移为 `processors/translator.py`,接口与 M3 兼容(`translate_in_place_news` 名字保持),内部走统一的 `LLMClient`(享受 token 累计统计)。`utils/translate.py` 已删除。

## 与 PLAN 的偏差

| 条款 | 偏差 |
|---|---|
| PLAN 第 7 节 M4 任务 1 "实现 `processors/llm_client.py`" | ✅ 完全对齐 |
| PLAN 第 7 节 M4 任务 2 "实现 4 个 processor" | ✅ 完全对齐(命名也一致) |
| PLAN 第 7 节 M4 任务 3 "每个 prompt 包含投资框架" | ✅ 通过 `build_system_prompt` 集中注入,4 个 processor 都走 |
| PLAN 第 7 节 M4 任务 4 "token 预算监控" | ✅ `LLMClient.cumulative` + `estimate_cost_cny` |
| PLAN 第 7 节 M4 任务 5 "LLM 失败降级到原始数据" | ✅ 模板每个 M4 区块加 if/else 降级 |
| PLAN 第 10 节"system prompt 基底" | ✅ `INVESTMENT_FRAMEWORK` 完全照搬 PLAN 文本 |
| ADR-0001 §3 "北京时间注入" | ✅ `build_system_prompt` 每次调用都注入 |

无方案级偏差。

## 测试

47 unit tests pass(原 30 M3 + M4 新增):
- `test_llm_client.py`(5):system prompt 拼装、北京时间、task_extra、AI 腔禁令
- `test_processors.py`(12):translator / news / macro / figures / sentiment 的 format/parse 纯函数
- 不调 DeepSeek API(LLM 是外部依赖,符合 PLAN 第 8 节"数据源 collector 不写 unit test")

## 暂停验证(用户 token 临时用尽)

代码全部完成 + 静态测试通过,但因用户 DeepSeek 账户 token 暂时用尽,**端到端真实运行 + 邮件发送 + token 成本实测**留待用户充值后再做。
M4 commit 不阻塞,只待真跑验证后:
- 主观感受:邮件是否"像专业财经摘要,不是 AI 腔"(PLAN 验收点)
- 客观:每天成本 ≤ ¥0.5(已估算 ~¥0.05,几乎没风险)

## 不做的(避免延伸)

- **不做完整 LLM 框架抽象**:`LLMClient` 只一个 `chat()` 方法,不做 streaming / function calling / 多模型抽象。M4 范围内只是 chat completion
- **不做 prompt 版本管理**:prompt 直接写在 processor 文件里,改 prompt 走 git diff。M4 之后用户主观觉得不好可以 PR 改;成熟稳定后再考虑配置化
- **不做单条 LLM 失败的细颗粒重试**:`LLMClient.chat` 失败直接返回 None,模板降级。重试由 `@retry` 装饰器层做(M3 已有,M4 暂不在 chat 上加,因为 LLM 调用本身耗时,3 次重试可能耗光预算)
- **不做异步并发调用**:4 个 processor 串行;一次完整跑 ~30 秒(各 5-10 秒),完全可接受
- **不做 13F-HR XML 解析**:PLAN 第 4 节模块 3c 后续工作,等用户 6 月 / 8 月 13F 季度更新窗口到来时再加
