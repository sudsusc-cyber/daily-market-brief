"""
主题生成主入口(Step 4 + 7 + 8)。

流程:
  1. 查缓存 (state/subject_cache.json),命中直接返回
  2. 调 DeepSeek (LLMClient.chat with system_override) 生成主题
  3. 验证;不通过 → 带 correction 反馈再调 1 次
  4. 仍不通过 → 走静态兜底
  5. 写缓存 + 写日志

主入口:
  generate_subject(data: SubjectData, *, llm: LLMClient, today_bj: date) -> str
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date
from pathlib import Path

from src.processors.subject import fallback, prompts, validator
from src.processors.subject.extractor import SubjectData
from src.processors.subject.solar_terms import season_of

logger = logging.getLogger(__name__)


# 主题缓存(按日期):防止同一天因 cron 双触发或多次调用产生不同主题
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
CACHE_PATH = _PROJECT_ROOT / "state" / "subject_cache.json"
LOG_PATH = _PROJECT_ROOT / "state" / "subject_generation.log"


# ──────────────  缓存读写  ──────────────

def _load_cache() -> dict[str, str]:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("subject.cache_read_failed err=%r", exc)
        return {}


def _save_cache(cache: dict[str, str]) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("subject.cache_write_failed err=%r", exc)


# ──────────────  日志(可选,不影响主流程)  ──────────────

def _log_generation(
    *,
    today_iso: str,
    data: SubjectData,
    llm_raw: str | None,
    final_subject: str,
    fallback_layer: str,  # "llm_pass1" / "llm_pass2" / "static_fallback" / "cache"
) -> None:
    """每次生成追加一行 JSON 日志,便于事后审查 prompt 漂移。"""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": today_iso,
            "data": data.to_dict(),
            "llm_raw": llm_raw,
            "final": final_subject,
            "layer": fallback_layer,
        }
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning("subject.log_write_failed err=%r", exc)


# ──────────────  LLM 调用(含重试)  ──────────────

def _call_deepseek(
    llm,
    user_prompt: str,
    *,
    timeout: int = 10,
) -> tuple[str | None, str | None]:
    """
    调用 LLMClient,返回 (text, error_msg)。
    - llm=None(离线/dryrun)→ 直接 (None, "llm_unavailable"),让调用方走兜底
    - timeout 10s(plan 要求)
    - 不在此重试(retry 由调用方控制 — 因为重试需要带 correction)
    """
    if llm is None:
        return None, "llm_unavailable"
    resp = llm.chat(
        user_prompt,
        system_override=prompts.SYSTEM_PROMPT,
        # DeepSeek V4-Flash 是 reasoning model,reasoning_tokens 常占 500-2000
        # max_tokens 必须给 reasoning + 最终输出留足空间,虽然主题本身只 9 字符
        max_tokens=2500,
        temperature=0.95,     # 用户要求更多样性(原 0.85 → 0.95)
        top_p=0.9,            # plan 要求
        timeout=timeout,
    )
    return resp.text, resp.error


# ──────────────  主入口  ──────────────

def generate_subject(
    data: SubjectData,
    *,
    llm,
    today_bj: date,
    use_cache: bool = True,
) -> str:
    """
    生成今日主题。永不抛异常,失败时返回兜底主题。
    """
    today_iso = today_bj.isoformat()
    # 当日季节(spring/summer/autumn/winter),供季节意象一致性校验用。
    # 节气名异常时退化为 None,此时 validator 不做季节检查(fail-open)。
    season = season_of(data.solar_term.current) if data.solar_term else None

    # 1. 查缓存
    if use_cache:
        cache = _load_cache()
        if today_iso in cache:
            cached = cache[today_iso]
            if validator.is_valid(cached, season=season):
                logger.info("subject.cache_hit date=%s subject=%r", today_iso, cached)
                _log_generation(
                    today_iso=today_iso, data=data, llm_raw=None,
                    final_subject=cached, fallback_layer="cache",
                )
                return cached

    # 2. 第一次 LLM 调用
    user_prompt = prompts.build_user_prompt(data)
    raw1, err1 = _call_deepseek(llm, user_prompt)
    if raw1 is not None:
        # 去除可能的引号 / 前后缀(尝试自救一下显而易见的 LLM 问题)
        cleaned = _light_clean(raw1)
        ok, reason = validator.validate(cleaned, season=season)
        if ok:
            logger.info("subject.llm_pass1 raw=%r → %r", raw1, cleaned)
            _save_to_cache_and_log(
                today_iso=today_iso, data=data, llm_raw=raw1,
                final=cleaned, layer="llm_pass1", use_cache=use_cache,
            )
            return cleaned
        logger.info("subject.llm_pass1_invalid raw=%r reason=%s", raw1, reason)
    else:
        logger.warning("subject.llm_pass1_error err=%s", err1)
        reason = err1 or "LLM 无响应"

    # 3. 第二次 LLM 调用(带 correction 反馈)
    time.sleep(1)  # 简单退避
    user_prompt2 = prompts.build_user_prompt(data, correction=reason)
    raw2, err2 = _call_deepseek(llm, user_prompt2)
    if raw2 is not None:
        cleaned2 = _light_clean(raw2)
        ok, reason2 = validator.validate(cleaned2, season=season)
        if ok:
            logger.info("subject.llm_pass2 raw=%r → %r", raw2, cleaned2)
            _save_to_cache_and_log(
                today_iso=today_iso, data=data, llm_raw=raw2,
                final=cleaned2, layer="llm_pass2", use_cache=use_cache,
            )
            return cleaned2
        logger.warning("subject.llm_pass2_invalid raw=%r reason=%s", raw2, reason2)
    else:
        logger.warning("subject.llm_pass2_error err=%s", err2)

    # 4. 兜底
    fb = fallback.static_fallback(data)
    logger.warning("subject.fallback_used → %r", fb)
    _save_to_cache_and_log(
        today_iso=today_iso, data=data, llm_raw=raw2 or raw1,
        final=fb, layer="static_fallback", use_cache=use_cache,
    )
    return fb


def _save_to_cache_and_log(
    *,
    today_iso: str, data: SubjectData,
    llm_raw: str | None, final: str, layer: str,
    use_cache: bool,
) -> None:
    if use_cache:
        cache = _load_cache()
        cache[today_iso] = final
        _save_cache(cache)
    _log_generation(
        today_iso=today_iso, data=data,
        llm_raw=llm_raw, final_subject=final, fallback_layer=layer,
    )


def _light_clean(raw: str) -> str:
    """对 LLM 输出做轻度清洗:去引号 / 去前后缀 / 半角空格 → 全角。
    不做激进改造(违规的就让 validator 拒绝,触发重试或兜底)。"""
    s = raw.strip()
    # 去常见包裹符号
    for pair in [('"', '"'), ("'", "'"), ('"', '"'), ("「", "」"), ("『", "』")]:
        if s.startswith(pair[0]) and s.endswith(pair[1]):
            s = s[1:-1].strip()
    # 去常见前缀
    for prefix in ["主题:", "主题:", "今日:", "输出:", "输出:"]:
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
    # 半角空格 → 全角(若中间出现半角空格,救一下;CJK 之间多半是全角分隔)
    if " " in s and "　" not in s:
        s = s.replace("  ", "　").replace(" ", "　")
    return s
