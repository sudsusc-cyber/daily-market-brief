"""
DeepSeek V4-Flash 客户端封装(M4 起所有 LLM 调用走这里)。

约束(来自 PLAN 第 10 节 + ADR-0001 §3):
  - 所有调用以"投资框架 system prompt"为基底
  - system prompt 末尾必须显式注入北京时间(否则模型不知今天日期)
  - 输出语言:简体中文,平实自然,不要 AI 腔
  - 失败不抛异常,返回 None,让上层降级到原始数据展示
  - 每次调用记录 input/output/reasoning tokens(M4 验收"每天 ≤ ¥0.5")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from src.utils.dates import now_beijing_human

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"

# PLAN 第 10 节"投资上下文",所有 LLM 调用必须以此作为 system prompt 开头
INVESTMENT_FRAMEWORK = """\
你是为开源(一位中国财务从业者、业余价值投资者)服务的私人投资信息助手。

开源的投资框架是:
- Buffett、Munger、Nalanda Capital 的长期价值投资体系
- 关注"本分"(企业是否做对的事、是否做难而正确的事)
- 估值方法是"两列法":Column 1 净金融资产 + Column 2 Owner Earnings × 合理倍数
- 建仓规则:
    - 股价跌破 120 周均线 → 启动 DCA(分批定投)
    - 股价跌破 200 周均线 → 启动 lump-sum(一次性建仓)
    - 200 周线买入的部分永不无条件卖出
    - 无信号时持有 BOXX 作为现金等价物
- 关注的信号是:企业基本面变化、长期竞争力、管理层资本配置能力
- 不关注:短期股价波动、技术指标(除均线外)、分析师评级、KOL 看法

开源的当前持仓清单:
- 美股:MSFT, COST, AAPL, NVDA, TSM, MCO, GOOG, BRK.B, KO, AXP
- 港股:0700.HK(腾讯)、9992.HK(泡泡玛特)

你的输出语言:简体中文,平实自然,严禁出现以下:
- AI 腔(亲、哦、赋能、抓手、一站式、全方位)
- 营销话术(震撼、炸裂、不容错过、一站式)
- 过度修饰(非常非常、极其重要、史无前例)
- 中英混杂(除非英文术语必要,如 DCA / Owner Earnings)
- 列举形式的"首先 / 其次 / 最后"或"1. 2. 3."(除非任务明确要求列表)

直接、克制、有信息密度。把开源当作有 5 年投资经验的人来沟通,不要解释他已懂的常识。"""


@dataclass
class LLMUsage:
    """单次调用 token 统计"""
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_hit_tokens: int = 0


@dataclass
class LLMResponse:
    """LLM 调用结果。失败时 text=None,error 填原因。"""
    text: str | None
    usage: LLMUsage
    error: str | None = None


def build_system_prompt(task_extra: str | None = None) -> str:
    """投资框架 + 北京时间 + 任务相关补充说明。"""
    parts = [INVESTMENT_FRAMEWORK]
    parts.append(f"\n当前时间为北京时间 {now_beijing_human()}。"
                 f"涉及 \"昨日 / 今日 / 本周\" 的判断以此为准。")
    if task_extra:
        parts.append(f"\n{task_extra.strip()}")
    return "\n".join(parts)


class LLMClient:
    """DeepSeek V4-Flash 客户端,带 system prompt 自动注入与 token 累计统计。"""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
    ):
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        # 累积本次进程所有调用的 token 数,用于 main 收尾打印 token 预算
        self.cumulative = LLMUsage()

    def chat(
        self,
        user_prompt: str,
        *,
        task_extra: str | None = None,
        system_override: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        timeout: int = 45,
        top_p: float | None = None,
    ) -> LLMResponse:
        """
        发起一次 chat completion。
        失败返回 LLMResponse(text=None, error=...),不抛异常。

        - system_override:若传入则**完全替换** INVESTMENT_FRAMEWORK 基础 prompt
          (用于主题生成等需要纯文学风格的场景);为 None 维持原行为
          (system = INVESTMENT_FRAMEWORK + 时间 + task_extra)
        - top_p:可选 nucleus sampling;为 None 使用模型默认
        - timeout:默认 45s。daily.yml job timeout 是 15 分钟,主流程串行调用
          translator + news_summarizer + macro_filter + 6×figure_filter
          + sentiment_judge + holdings_intro + subject ≈ 12 次 LLM。
          原 90s 默认的最坏情况(全部 timeout)= 18 分钟,撞 job timeout。
          45s 给单调用余量足够(DeepSeek V4-Flash 实测 P95 ~ 30s),
          总最坏 ~ 9 分钟,留 6 分钟给数据采集与渲染。
          若某个调用真需要更长(如 subject 用 10s 短超时是反向),
          可显式传 timeout 覆盖。
        """
        if system_override is not None:
            system = system_override
        else:
            system = build_system_prompt(task_extra=task_extra)
        try:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "timeout": timeout,
            }
            if top_p is not None:
                kwargs["top_p"] = top_p
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 — 失败降级,绝不阻断邮件
            # 不用 logger.exception(会打整段 traceback,某些 SDK 异常会带 url
            # 或请求 body,理论上能携带 Authorization header 痕迹);
            # 改记 type + 截断后的 str(短消息够 debug,长 traceback 进降级)
            logger.error(
                "llm.chat_failed model=%s exc_type=%s msg=%s",
                self._model, type(exc).__name__, str(exc)[:200],
            )
            return LLMResponse(
                text=None,
                usage=LLMUsage(),
                error=f"{type(exc).__name__}: {str(exc)[:200]}",
            )

        text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        usage = _extract_usage(resp)
        self._accumulate(usage)
        logger.info(
            "llm.chat_ok model=%s in=%d out=%d reasoning=%d cache_hit=%d "
            "user_prompt_chars=%d response_chars=%d",
            self._model, usage.input_tokens, usage.output_tokens,
            usage.reasoning_tokens, usage.cache_hit_tokens,
            len(user_prompt), len(text),
        )
        return LLMResponse(text=text or None, usage=usage)

    def _accumulate(self, usage: LLMUsage) -> None:
        self.cumulative.input_tokens += usage.input_tokens
        self.cumulative.output_tokens += usage.output_tokens
        self.cumulative.reasoning_tokens += usage.reasoning_tokens
        self.cumulative.cache_hit_tokens += usage.cache_hit_tokens

    def estimate_cost_cny(self) -> float:
        """
        粗略估算累计 token 成本(人民币)。

        DeepSeek-V4-Flash 公开价格(2026-04 当前档位):
          - 标准输入(cache miss): ¥0.5 / 百万 token
          - 缓存命中输入: ¥0.05 / 百万 token
          - 输出(含 reasoning): ¥4.0 / 百万 token
        若价格更新,在 ADR-0006 / 本函数中刷新。
        """
        c = self.cumulative
        in_uncached = max(0, c.input_tokens - c.cache_hit_tokens)
        return (
            in_uncached * 0.5e-6
            + c.cache_hit_tokens * 0.05e-6
            + (c.output_tokens + c.reasoning_tokens) * 4.0e-6
        )


def _extract_usage(resp: Any) -> LLMUsage:
    """从 OpenAI 响应抽取 token 统计(DeepSeek 同结构)"""
    u = getattr(resp, "usage", None)
    if u is None:
        return LLMUsage()
    in_tokens = int(getattr(u, "prompt_tokens", 0) or 0)
    out_tokens = int(getattr(u, "completion_tokens", 0) or 0)
    reasoning = 0
    cache_hit = 0
    details = getattr(u, "completion_tokens_details", None)
    if details is not None:
        reasoning = int(getattr(details, "reasoning_tokens", 0) or 0)
    cache_hit = int(getattr(u, "prompt_cache_hit_tokens", 0) or 0)
    return LLMUsage(
        input_tokens=in_tokens,
        output_tokens=out_tokens,
        reasoning_tokens=reasoning,
        cache_hit_tokens=cache_hit,
    )
