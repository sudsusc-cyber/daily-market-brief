# ADR-0014 — DeepSeek 格式化任务韧性与宏观视野质量门槛

**状态**: 已接受  
**日期**: 2026-08-04  
**触发**: `deepseek-v4-flash` 在相同代码下将 3999-4000 个输出 token
全部用于 reasoning，可见正文为空，宏观视野因而降级成原始 RSS 列表。

## 背景与证据

- 2026-07-30 run `30589088214`: `reasoning=1614` / `response_chars=208`，
  `macro_filter.ok`。
- 2026-07-31 run `30671660406`: `reasoning=3999` / `response_chars=0`，
  `ReasoningStarved`。
- 2026-08-04 run `30860734678`: `reasoning=4000` / `response_chars=0`，
  `ReasoningStarved`。
- 三次 run 均使用同一 commit `677e884`，排除代码修改导致的回归。

DeepSeek V4 思考模式默认开启。对翻译、格式化摘要这类任务，
让 reasoning 与可见正文共享较小的 `max_tokens` 会导致结果随服务端行为漂移。

## 决策

1. `LLMClient.chat()` 增加 `thinking` 显式开关，通过 DeepSeek OpenAI
   兼容参数 `extra_body={"thinking": {"type": "disabled"}}` 传递。
2. 标题翻译和宏观摘要明确使用非思考模式。
3. 翻译首轮缺失部分编号时，仅对缺失项再试一次；不重做已成功项。
4. 宏观摘要空输出或全部段落无有效脚注时，带修正指令再试一次。
5. 两次仍失败时，邮件只展示受控占位语，不展示原始 RSS 标题。
6. 通过 `.quality-alert.txt` 在 GitHub Actions 中生成 warning annotation 与
   step summary，但不将 job 标红，避免双 cron 幂等逻辑误触发重复邮件。

## 后果

- 宏观视野的已验收主题分段版式不再因模型思考长度漂移而失效。
- 原始 RSS 仍用于加工和引用回查，但不再作为用户可见的宏观降级内容。
- 邮件送达 SLO 与内容质量告警分离：可继续发信，但降级不再静默。
- 非思考模式下 `temperature` 重新有效，格式化任务的输出更稳定。

## 不做

- 不仅靠把 `max_tokens` 从 4000 上调到更大值；这只延后耗尽点，
  没有消除思考模式对格式化任务的不确定性。
- 本次不批量改变 sentiment / thesis / subject 等需要判断或文学生成的调用。
