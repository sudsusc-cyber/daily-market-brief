# ADR-0008 — M4 验收收尾 + 切 session 给 M5 的 hand-off 清单

- 日期:2026-04-30
- 状态:已采纳
- 上一份:ADR-0007(M6 cron 调度)
- 触发:用户准备结束当前 session 切去新 session 做 M5,要求把"心里清楚但未成文"的决策落地

## A. M4 验收记录

### A.1 M4 实际通过的形式

PLAN 第 7 节 M4 的三条验收标准:

| 验收项 | 状态 | 凭证 |
|---|---|---|
| 邮件内容从"原始列表"变成"自然段落" | ✅ | 实测 III 区块"公司分行" + V 区块"主题分段叙述" + IV 区块关键观点提炼 |
| 用户连续看 3 天的邮件,主观感受是"像专业财经摘要,不是 AI 腔" | ⏸ 单日已通过 | M4 主体首次跑(2026-04-30 13:53)+ 多轮 typography 内修复,用户最后一次反馈未拒绝;3 天连续累积观察留待生产稳定运行后 |
| 每天 LLM 成本 ≤ ¥0.5 | ✅ | 实测多次跑 ¥0.04 - ¥0.10 区间,远低于上限。详 ADR-0006 §4 |

### A.2 M4 验收期间 4 轮迭代

每轮都是用户给具体指令 → 我落地 → 跑邮件验证。所有改动都已 commit。
ADR 记录入口:

| 轮次 | 主题 | 入口 |
|---|---|---|
| 0 | M4 主体(4 processor + translator + token 监控) | ADR-0006 |
| 1 | 排版基本功 + structured prompts + footnotes 方案 B | commit `161caff` "fix(m4): typography, structured prompts, footnote citations" |
| 2 | 序号字体协调 / 脚注横排 / Word 蓝 / 但斌入清单 / IV 字体统一 / prompt 禁止 "XXX 说" 前缀 | commit `0383f4f` |
| 3 | M6 cron 调度修正 | ADR-0007 |

## B. 没明文记录但已确认的决策

### B.1 figures 监控人物清单(最终)

- ✅ **黄仁勋**(英文搜索 `"Jensen Huang"`),24h 命中 ~96 raw / ~40 通过过滤
- ✅ **巴菲特**(英文搜索 `"Warren Buffett"`),24h ~100 / ~9
- ✅ **但斌**(中文搜索 `"但斌"`),24h ~12 / ~1。每天通常 1-2 候选,LLM 第二道筛选偶尔过 0。可接受
- ❌ **段永平**:实测 24h raw=2 / passed=0,Google News 不抓他(他主要在雪球)。PLAN 第 11 节原已放弃,本次实测再次确认
- ❌ **李录**:实测 24h raw=2 / passed=0,频率太低
- 后续若有第 5 个人选,先用 `_fetch_google_news` 实测稳定性再加

### B.2 链接颜色改 Word 蓝 `#0563C1`(原 ADR-0006 写的是 oxblood)

- ADR-0006 §"4. 脚注角标样式" 当时写的是 oxblood `#7A1F2B`
- 用户 M4 验收期间明确要求"Word 文档默认链接蓝"
- 现状:**全文链接(脚注 + `<sup>[N]</sup>` 内 anchor + IV `[Source]`)统一 Word 蓝 `#0563C1`**
- ADR-0006 §4 的描述视为被本 ADR 覆盖

### B.3 关键观点禁止"XXX 说"前缀

- 人物姓名已是 IV 区块的小标题,正文里再写"黄仁勋说" / "巴菲特表示" 是冗余
- prompt(`processors/figure_filter.py::_TASK_INSTRUCTION`)显式禁止此类前缀
- 若未来 LLM 反复违反,考虑 Python 端用 regex 后处理(暂未实施)

### B.4 IV 区块的 0 候选展示

- 黄仁勋 / 巴菲特 / 但斌 三人都进 figures 列表后,即使某人 0 candidate,模板仍渲染该人物小标题 + "昨日无发言。"
- 不会让"该人物完全消失",方便用户每天扫一眼即知是否有动态

### B.5 当前 PNG logo 处理流程(M2/M3 沉淀)

`scripts/fetch_logos.py` 已具备的步骤(后续维护参考):

1. 默认 Google S2 favicon(`https://www.google.com/s2/favicons?domain=<domain>&sz=128`)
2. `LOGO_OVERRIDES` 字典里特定 ticker 走 Wikimedia 或 Financial Modeling Prep
   (TSM → FMP / COST → FMP / BRK.B → FMP)
3. 系统 `sips` 可用时缩到 ≤128px(macOS 自带,Linux 跳过)
4. Python `struct` 重写 PNG,strip eXIf / gAMA / cHRM 等装饰 chunks
   (微信 webview 兼容性必需,详 ADR-0005 §1)

新增持仓时:更新 `src/config.py::HOLDINGS` + 重跑 fetch_logos.py。

## C. 给 M5 的 hand-off

### C.1 M5 范围对照(PLAN 第 7 节)

PLAN 原 M5 是"HTML 精美化 + 兼容性"。但精美设计 / logo / typography 都已在 M2 / M3 / M4 期间落地(详 ADR-0002 §"M5 的新边界")。

**M5 实际工作收窄到**:

1. **跨邮件客户端兼容性测试**(QQ webmail / Gmail / Outlook 网页版 / 微信 webview / iOS Mail / 安卓 Gmail)
   - 截图对比,记录差异
   - 修任何客户端的破版/不渲染问题(已知:微信 webview 对带 EXIF chunks 的 PNG 不渲染,已修;其他可能在 M5 阶段发现)
2. **暗色模式适配**:确保深色背景下不变成"一团黑"
3. **打印友好样式**(用户可能想存档)
4. **设计微调**:基于累积反馈做小幅修正,不再整体重设计

### C.2 M5 不做的(避免范围爬升)

- ❌ 重新选强调色(oxblood + Word 蓝 + 涨绿森林绿 + 跌红暗红 已锁定)
- ❌ 重新选字体(思源宋体优先 + Charter / Cambria / Georgia 已锁定)
- ❌ 重选排版字号 / 行距(16px / 1.9 / letter-spacing 0.02em 已锁定)
- ❌ 重写 LLM prompt(M4 已稳定)
- ❌ 改 collectors / processors 数据结构(M4 已稳定)

### C.3 M5 启动前用户需做的事

- 暗色模式适配测试可能需要用户在 iOS Mail / 安卓 Gmail 切换深浅模式截图
- 跨客户端测试理想情况下需要用户在 Gmail / Outlook 网页版各打开一封邮件让 CC 比对效果
  (CC 可以让用户截图发回,或用户手机 / 桌面浏览器各打开一次)

### C.4 M5 期间会动到的文件预期

- `src/renderer/templates/email.html.j2`:小幅样式微调
- (新增)`docs/email-rendering-matrix.md`:6 个客户端的渲染截图与差异说明
- (新增)`docs/decisions/0009-m5-rendering-compat.md`:本期决策

### C.5 M5 不会动到的文件

- `src/collectors/*` / `src/processors/*` / `src/utils/*` / `src/main.py`:与渲染兼容性无关
- `assets/logos/*.png`:已规格化(方形 ≤128 + strip metadata)

## D. M6 已经锁定但未实施的事

ADR-0007 已锁:

- cron `0 0 * * 1-5`(北京周二-周六 08:00)
- 美股节假日 → 跳过
- 港股节假日不单独判断
- secrets 用 GitHub Secrets,不入库

M6 启动时还需要决策:

- 节假日表存哪(我建议 `state/us_holidays_2026.json`,M6 一次性手抄 + 每年 11 月底刷新)
- 邮件失败的告警邮箱(用 `EMAIL_RECIPIENT` 还是另设)
- GH Actions 跑完后 `state/pushed_figures.json` / `state/last_13f.json` 怎么 commit 回 git
  (workflow 末尾加 git commit + git push step)
- 失败时是否自动 retry 1 次(PLAN 第 7 节 M6 任务 1 提到)

## E. 当前仓库状态(切 session 时)

- 当前分支:`claude/sharp-meninsky-e43ea3`(worktree)
- 已 push:`origin/main` = `84afb9e`(及之前 9 个 commit)
- 清单:M1 → M2 → M2 logo → TSM 微信修复 → M3 → M3 patches → M4 → M4 内修复 → M4 v2 → ADR-0007 → 本 ADR
- worktree 工作树:干净(本 ADR commit 后)
- 47 unit tests pass(`uv run pytest tests/ -q`)
- 实测端到端跑通:邮件 14:38 已发,token 成本 ¥0.05-0.10/天

新 session 开始时直接 `git pull origin main` 即可。
