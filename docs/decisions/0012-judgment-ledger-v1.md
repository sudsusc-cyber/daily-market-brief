# ADR-0012: Judgment Ledger（长期判断）V1

**日期**: 2026-05-04
**状态**: 已实施
**涉及模块**: `src/processors/thesis/`（8 个文件）

## 背景

日报各模块（持仓信号、公司新闻、宏观、关键发言、前沿模型）处理的是“昨日发生了什么”。
缺少一个模块持续跟踪“这些事件对长期判断意味着什么”——判断是否被强化、削弱、或出现新变量。

Judgment Ledger 的设计目标：
- 从每日已筛选内容中抽取与长期判断直接相关的 evidence
- 维护一组主题的状态机，追踪判断的成熟度
- 每天最多输出 3 条“渐明”事件到邮件正文

## 核心设计

### 状态机

```
candidate → emerging → core → stable → dormant
                ↑         ↑        ↑        │
                └─────────┴────────┴────────┘ (reactivated)
```

| 状态 | 含义 | 进入条件 |
|---|---|---|
| `candidate` | 新主题，观察中 | 任意新 evidence（自动） |
| `emerging` | 多源验证中 | ≥3 条 evidence + ≥2 个不同 source |
| `core` | 已确认，进入跟踪 | emerging 满足 + ≥60 天 + ≥5 条 90d evidence + 有权威源 |
| `stable` | 确认但停滞 | core 状态 + 半 cadence 无 strong support + 90d evidence < 5 |
| `dormant` | 长期无新证据 | core/stable + stale_after_days 过期或 cooldown 满 |

状态迁移规则在 `rules.py:run_state_transitions()` 中，按 candidate→emerging→core→stable→dormant 顺序执行，互斥（`elif`）。

### Cadence（节奏）

每个主题有 cadence 属性，影响 stale 判定和 stable 迁移阈值。

| Cadence | 含义 | stale_after_days | 典型主题 |
|---|---|---|---|
| `fast` | 变化快 | 90d | 监管、出口管制 |
| `quarterly` | 季度节奏 | 180d | AI capex、定价权、利润率 |
| `slow` | 年度节奏 | 360d | 资本配置、管理层、回购 |
| `structural` | 结构变化 | 720d | 品牌、护城河、行业结构 |

Cadence 通过关键字匹配 + horizon 退化确定（`cadence.py:resolve_cadence()`）：theme 关键字子串匹配 `THEME_CADENCE_HINTS` 表，未命中则按 LLM 给的 horizon 退化（structural→structural, multi_year→slow, quarterly→quarterly）。

### evidence_id 设计

`evidence_id = sha1(date|source_section|source_name|theme|normalized_text)[:16]`

- 用于幂等去重：同一天同一来源同一 theme 同一事实，多次 append 只存一条
- normalized_text 去空白、lowercase，确保“同一事实不同写法”仍命中
- 16 位 hex 足够区分（碰撞概率 ~1/2^64）

### 渐明事件（substantiate）

每天只输出 core 主题中“今日收到 strong support”的事件：
- 触发条件：`direction==support ∧ strength>=4`（`is_strong_support()`）
- 上限：3 条（按 strength 排序）
- cooldown：同一主题 30 天内不重复展示
- 渲染格式：去标题、去 ticker、去 label，仅保留 `❀` fleuron 分隔线 + 居中红色 `「thesis」获得新证据支持。`

### 核心约束

- `CORE_CAP = 12`：core 主题上限，超出时按 status 优先级 + 最近 evidence 日期降级
- `COOLDOWN_DAYS = 30`：同一主题在邮件中的最小展示间隔
- `EMERGING_MIN_EVENTS = 3`：candidate→emerging 最少 evidence 数
- `EMERGING_MIN_SOURCES = 2`：至少两个不同数据源
- `CORE_MIN_ELAPSED_DAYS = 60`：从首次出现到进 core 最短天数
- `CORE_MIN_EVENTS_90D = 5`：进 core 需要近 90 天至少 5 条 evidence
- `MAX_EVIDENCE_ITEMS = 8`：每天 LLM 抽取上限

### 权威源白名单

`has_authoritative_source()` 双重判定：
1. `source_section ∈ {berkshire, frontier_labs}`：直接信任
2. `source_name` 在 `AUTHORITATIVE_NAMES` 白名单中（含 display name 和域名，如 “OpenAI”“openai.com”“Financial Times”“ft.com”），lowercase 子串匹配

### 持久化

- `state/thesis_evidence_<YYYY>.jsonl`：按年分文件，每天 append，幂等去重
- `state/thesis_state.json`：全量 theme 状态快照，每次 run 后覆盖写
- 两者均由 LLM 抽取 → 状态机 → 渲染的流水线消费

## 为什么这样设计

1. **状态机而非简单列表**：长期判断需要时间验证。candidate 是“注意到了”，core 是“确认了”，stable 是“确认但近期没动静”。状态区分让读者知道哪些判断在活跃更新、哪些在沉淀。

2. **cadence 而非固定 stale 天数**：AI capex 每个季度财报都有新证据，品牌护城河的变化以年为单位。用同一套过期规则会导致快节奏主题被过早休眠、慢节奏主题长期占位。

3. **cooldown 而非每次都展示**：core 主题可能连续多天收到 strong support，每天展示会让读者疲劳。30 天 cooldown 确保每条展示都是“你有一阵子没看到的新进展”。

4. **evidence_id 用 sha1 而非逐字段比对**：evidence 来自多个 LLM 调用（figure_filter、news_summarizer 等），同一个事实可能在两个 source_section 出现。sha1 归一化后避免重复计数。

## 渲染演进

V1 渲染经历了多次迭代（详见 commits `1746579` → `2f1175f` → `42d18de`）：

- 初版：标题“长期判断 / Judgment Ledger” + 左对齐 label“渐明” + ticker 前缀
- 去模板化：移除 label 徽标，改为 `「TICKER ｜ 事实描述」`
- 最终版：完全删除标题区块，改为 `❀` fleuron 分隔线 + 居中红色 `「断言」获得新证据支持。`

核心原则：消除“打卡感”——红色 `「」` 本身已是独特的视觉语法，不需要标题再说一遍。

## 未来改进方向

- V2：展示 risk 事件（当前仅展示 support，risk 只在状态迁移时生效）
- V2：new_variable 事件（LLM 标注的新维度，尚未映射到具体判断）
- 参数调优：cooldown 21 天、core cap 12 可能需要根据实际生产数据调整

---

## Amendments

### 2026-05 — `COOLDOWN_DAYS` 从 30 改为 21

上文初版定义 `COOLDOWN_DAYS = 30`，但 [src/processors/thesis/rules.py](../../src/processors/thesis/rules.py) 已在 PR #40 期间调整为 21 天。原因：30 天对快主题（regulatory / antitrust / geopolitical 等 fast cadence）显得过长，core 主题获得新证据后要等 30 天才能再次展示，会让"渐明"事件长期空白。21 天是当前折中值；rules.py 内 TODO 注释提到，等去重上线观察 30+ 天后可能改为 cadence-aware 表（fast 主题更短、structural 主题更长）。

校准期(2026-05 起)：`company_news` / `macro_news` 引入 7 天 hash + 同日模糊去重后,evidence 流入量较此前下降约一半。这是历史去重缺失导致的虚高归正,**不调整 EMERGING/CORE 阈值**,让 90 天滚动窗口自然将虚高 evidence 滚出。预期 30-90 天内会有一波 core → stable 降级,日志 `thesis.demotions_today` 跟踪。
