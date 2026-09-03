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
import re
import time
from dataclasses import dataclass
from typing import Any

import requests
from openai import OpenAI

from src.config import BUY_STRATEGIES
from src.utils.dates import now_beijing_human
from src.utils.secrets import redact_secrets

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"
_FLASH_MODEL_RE = re.compile(r"^deepseek-v(?P<version>\d+(?:\.\d+)*)(?:-[a-z0-9]+)*-flash$")

_BUY_RULES_CONTEXT = "\n".join(
    f"    - {', '.join(strategy.tickers)}: "
    + (f"现价 ≤ {strategy.line_labels[0]}均线 → DCA(小额固定定投); "
       if strategy.dca_line else "不设 DCA; ")
    + f"现价 ≤ {strategy.line_labels[-1]}均线 → lump-sum(大额买入)"
    for strategy in BUY_STRATEGIES
)

# PLAN 第 10 节"投资上下文",所有 LLM 调用必须以此作为 system prompt 开头
INVESTMENT_FRAMEWORK = f"""\
你是为开源(一位中国财务从业者、业余价值投资者)服务的私人投资信息助手。

开源的投资框架是:
- Buffett、Munger、Nalanda Capital 的长期价值投资体系
- 关注"本分"(企业是否做对的事、是否做难而正确的事)
- 估值方法是"两列法":Column 1 净金融资产 + Column 2 Owner Earnings × 合理倍数
- 建仓规则:
{_BUY_RULES_CONTEXT}
    - 250 日指 250 个交易日; 港股与美股独立分组展示
    - 信号每天持续显示当前区间,不是首次跌破提醒; 大额优先,不满足则无信号
    - 200 周线买入的部分永不无条件卖出
    - 无信号时持有 BOXX 作为现金等价物
- 关注的信号是:企业基本面变化、长期竞争力、管理层资本配置能力
- 不关注:短期股价波动、技术指标(除均线外)、分析师评级、KOL 看法

开源的当前持仓清单:
- 美股:MSFT, COST, AAPL, NVDA, TSM, MCO, GOOG, BRK.B, KO, AXP, MA, LIN
- 港股:0700.HK(腾讯)、9992.HK(泡泡玛特)
- ETF:QQQM(Invesco Nasdaq 100 ETF),与 MSFT 同组买入线,按独立 v1.6 乐观情景 ETF 公允价值规则取数

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


def _deepseek_flash_version_key(model_id: str) -> tuple[int, ...] | None:
    """返回 deepseek-v*-flash 的可排序版本号;非 flash 模型返回 None。"""
    match = _FLASH_MODEL_RE.match(model_id.strip().lower())
    if not match:
        return None
    return tuple(int(part) for part in match.group("version").split("."))


def _select_latest_flash_model(model_ids: list[str]) -> str | None:
    candidates = [
        (key, model_id)
        for model_id in model_ids
        if (key := _deepseek_flash_version_key(model_id)) is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def resolve_latest_flash_model(
    api_key: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    fallback: str = DEFAULT_MODEL,
    timeout: int = 10,
) -> str:
    """
    调 DeepSeek /models 自动选择最新 deepseek-v*-flash。

    失败时回退到 fallback,不阻断日报。只自动跟随 flash 系列,避免误切到更贵或更慢的
    pro / speciale / legacy 模型。
    """
    try:
        resp = requests.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        model_ids = [
            str(item.get("id", "")).strip()
            for item in (data.get("data") or [])
            if isinstance(item, dict) and item.get("id")
        ]
        selected = _select_latest_flash_model(model_ids)
        if selected:
            if selected != fallback:
                logger.info("llm.model_resolved selected=%s fallback=%s", selected, fallback)
            else:
                logger.info("llm.model_resolved selected=%s", selected)
            return selected
        logger.warning("llm.model_resolve_no_flash fallback=%s ids=%s", fallback, model_ids[:10])
    except Exception as exc:  # noqa: BLE001
        # 与其他 collector 一致:异常 str 可能含 query-string 凭据,先脱敏
        logger.warning(
            "llm.model_resolve_failed fallback=%s exc_type=%s msg=%s",
            fallback, type(exc).__name__, redact_secrets(str(exc))[:200],
        )
    return fallback


class LLMClient:
    """DeepSeek 客户端,带 system prompt 自动注入与 token 累计统计。"""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        max_retries: int = 0,
        total_timeout_seconds: float = 480.0,
    ):
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=max(0, max_retries),
        )
        self._model = model
        self._deadline = time.monotonic() + max(1.0, total_timeout_seconds)
        # 累积本次进程所有调用的 token 数,用于 main 收尾打印 token 预算
        self.cumulative = LLMUsage()

    def chat(
        self,
        user_prompt: str,
        *,
        task_extra: str | None = None,
        system_override: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.3,
        timeout: int = 45,
        top_p: float | None = None,
        thinking: bool | None = None,
    ) -> LLMResponse:
        """
        发起一次 chat completion。
        失败返回 LLMResponse(text=None, error=...),不抛异常。

        - system_override:若传入则**完全替换** INVESTMENT_FRAMEWORK 基础 prompt
          (用于主题生成等需要纯文学风格的场景);为 None 维持原行为
          (system = INVESTMENT_FRAMEWORK + 时间 + task_extra)
        - top_p:可选 nucleus sampling;为 None 使用模型默认
        - thinking:显式开关 DeepSeek 思考模式。None 保留服务端默认;
          False 用于翻译、格式化摘要等确定性任务，避免思考 token
          耗尽 max_tokens 后没有可见正文。
        - timeout:默认 45s。daily.yml job timeout 是 15 分钟,主流程串行调用
          translator + news_summarizer + macro_filter + 最多 13×figure_filter
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
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            return LLMResponse(
                text=None,
                usage=LLMUsage(),
                error="LLMStageDeadlineExceeded: cumulative LLM budget exhausted",
            )
        effective_timeout = max(1.0, min(float(timeout), remaining))
        try:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "timeout": effective_timeout,
            }
            if top_p is not None:
                kwargs["top_p"] = top_p
            if thinking is not None:
                kwargs["extra_body"] = {
                    "thinking": {"type": "enabled" if thinking else "disabled"},
                }
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 — 失败降级,绝不阻断邮件
            # 不用 logger.exception(会打整段 traceback,某些 SDK 异常会带 url
            # 或请求 body,理论上能携带 Authorization header 痕迹);
            # 改记 type + 截断后的 str(短消息够 debug,长 traceback 进降级)。
            # str(exc) 经 redact_secrets 脱敏 query-string 凭据,与其他 collector
            # 保持一致;error 字段也用脱敏版,因其会经 LLMResponse 流回上层做日志/兜底文案。
            redacted_msg = redact_secrets(str(exc))[:200]
            logger.error(
                "llm.chat_failed model=%s exc_type=%s msg=%s",
                self._model, type(exc).__name__, redacted_msg,
            )
            return LLMResponse(
                text=None,
                usage=LLMUsage(),
                error=f"{type(exc).__name__}: {redacted_msg}",
            )

        text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        usage = _extract_usage(resp)
        error = None
        if not text and usage.reasoning_tokens > max_tokens * 0.7:
            error = (
                "ReasoningStarved: output text empty "
                f"(reasoning={usage.reasoning_tokens}, max_tokens={max_tokens})"
            )
            logger.warning(
                "llm.reasoning_starved model=%s reasoning=%d max_tokens=%d "
                "output text empty — reasoning 可能挤空了输出预算",
                self._model, usage.reasoning_tokens, max_tokens,
            )
        elif not text:
            error = "EmptyOutput: model returned no visible text"
        self._accumulate(usage)
        logger.info(
            "llm.chat_ok model=%s in=%d out=%d reasoning=%d cache_hit=%d "
            "user_prompt_chars=%d response_chars=%d",
            self._model, usage.input_tokens, usage.output_tokens,
            usage.reasoning_tokens, usage.cache_hit_tokens,
            len(user_prompt), len(text),
        )
        return LLMResponse(text=text or None, usage=usage, error=error)

    def search_web(
        self,
        user_prompt: str,
        *,
        allowed_domains: tuple[str, ...],
        task_extra: str | None = None,
        max_output_tokens: int = 1600,
        timeout: int = 60,
        market_data: bool = False,
    ) -> LLMResponse:
        """强制 DeepSeek Responses API 执行网页搜索。

        仅作官方文件发现的备用通道。调用方仍须校验域名、打开原文、核对发布时间
        与 document_id；本方法返回的文字绝不能直接成为估值输入。
        """
        if not allowed_domains:
            return LLMResponse(
                text=None,
                usage=LLMUsage(),
                error="WebSearchDomainAllowlistRequired",
            )
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            return LLMResponse(
                text=None,
                usage=LLMUsage(),
                error="LLMStageDeadlineExceeded: cumulative LLM budget exhausted",
            )
        effective_timeout = max(1.0, min(float(timeout), remaining))
        domains = ", ".join(allowed_domains)
        guard = (
            "你正在执行官方财务文件发现，不是一般新闻搜索。"
            f"只允许返回这些域名的原始文件或公司公告：{domains}。"
            "必须给出完整 URL、文件标题、正式发布时间、报告期间和公告编号；"
            "若无法在官方原文核实任一字段，明确返回 NOT_VERIFIED。"
        )
        if market_data:
            guard = (
                f"只从这些域名读取公开基金和指数数据：{domains}。"
                "必须先执行网页搜索，再输出最终 JSON；找到核心字段后立即回答。"
                "按用户指定 JSON 字段返回数值、真实数据日期和来源 URL；"
                "不可将抓取日期当作数据日期，不可用示例或记忆填补缺失数据。"
            )
        system = build_system_prompt(task_extra="\n".join(filter(None, [task_extra, guard])))
        try:
            search_options = {}
            if market_data:
                # Fixed-schema extraction does not need hidden reasoning. Forced
                # search can consume every continuation without a final message.
                search_options = {
                    "reasoning": {"effort": "none"},
                    "text": {"format": {"type": "json_object"}},
                }
            response = self._client.responses.create(
                model=self._model,
                instructions=system,
                input=user_prompt,
                tools=[{"type": "web_search"}],
                tool_choice="auto" if market_data else {"type": "web_search"},
                max_output_tokens=max_output_tokens,
                timeout=effective_timeout,
                **search_options,
            )
        except Exception as exc:  # noqa: BLE001
            redacted_msg = redact_secrets(str(exc))[:200]
            logger.error(
                "llm.web_search_failed model=%s exc_type=%s msg=%s",
                self._model,
                type(exc).__name__,
                redacted_msg,
            )
            return LLMResponse(
                text=None,
                usage=LLMUsage(),
                error=f"{type(exc).__name__}: {redacted_msg}",
            )
        text = _extract_responses_text(response)
        output_types = [
            item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
            for item in (getattr(response, "output", None) or [])
        ]
        logger.info(
            "llm.web_search_structure status=%s output_types=%s incomplete=%s",
            getattr(response, "status", None),
            output_types,
            getattr(response, "incomplete_details", None),
        )
        usage = _extract_responses_usage(response)
        self._accumulate(usage)
        error = None if text else "EmptyOutput: web search returned no visible text"
        if market_data and "web_search_call" not in output_types:
            text = ""
            error = "WebSearchNotExecuted: market data requires live search"
        logger.info(
            "llm.web_search_ok model=%s in=%d out=%d reasoning=%d domains=%s response_chars=%d",
            self._model,
            usage.input_tokens,
            usage.output_tokens,
            usage.reasoning_tokens,
            domains,
            len(text),
        )
        return LLMResponse(text=text or None, usage=usage, error=error)

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
            # completion_tokens 已包含 reasoning_tokens，不能重复计费。
            + c.output_tokens * 4.0e-6
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


def _extract_responses_usage(resp: Any) -> LLMUsage:
    """Responses API 使用 input/output 命名，与 chat completion 不同。"""
    usage = getattr(resp, "usage", None)
    if usage is None:
        return LLMUsage()
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    cache_hits = int(getattr(input_details, "cached_tokens", 0) or 0)
    reasoning = int(getattr(output_details, "reasoning_tokens", 0) or 0)
    return LLMUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning,
        cache_hit_tokens=cache_hits,
    )


def _extract_responses_text(resp: Any) -> str:
    """兼容 Responses API 的 output_text 与 DeepSeek 的 output 内容数组。"""
    direct = str(getattr(resp, "output_text", "") or "").strip()
    if direct:
        return direct
    output = getattr(resp, "output", None)
    if not isinstance(output, (list, tuple)):
        return ""
    chunks: list[str] = []
    for item in output:
        item_type = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
        if item_type not in (None, "message"):
            continue
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
        if not isinstance(content, (list, tuple)):
            continue
        for part in content:
            part_type = part.get("type") if isinstance(part, dict) else getattr(part, "type", None)
            if part_type not in (None, "text", "output_text"):
                continue
            text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
            if text:
                chunks.append(str(text))
    return "".join(chunks).strip()
