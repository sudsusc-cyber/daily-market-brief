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
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError(f"expected object, got {type(data).__name__}")
        return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("subject.cache_read_failed err=%r", exc)
        return {}


_CACHE_KEEP_DAYS = 30  # 缓存只保留最近 N 个 BJT 日期,避免长期 cache 文件膨胀


def _prune_cache(cache: dict[str, str], keep_days: int = _CACHE_KEEP_DAYS) -> dict[str, str]:
    """按 ISO 日期字符串字典序排序(YYYY-MM-DD 字典序 = 时间序),只留最新 N 条。"""
    if len(cache) <= keep_days:
        return cache
    keys = sorted(cache.keys())
    return {k: cache[k] for k in keys[-keep_days:]}


def _save_cache(cache: dict[str, str]) -> None:
    """原子写入 + 旧日期清理:避免 cache 文件随天数无限增长。"""
    try:
        cache = _prune_cache(cache)
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(CACHE_PATH.suffix + ".tmp")
        tmp.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(CACHE_PATH)
    except Exception as exc:  # noqa: BLE001
        logger.warning("subject.cache_write_failed err=%r", exc)


# ──────────────  日志(可选,不影响主流程)  ──────────────

# 日志大小上限 — 每行 ~1-3 KB(SubjectData 含 email_html_excerpt 等),
# 1 MB 约 300-1000 行(1-3 年)。超限只保留尾部 ~一半,丢弃最早的记录。
# 不做按天 rotate(简化;主用途是审查最近 prompt 漂移)。
_LOG_MAX_BYTES = 1_000_000   # 1 MB
_LOG_KEEP_BYTES = 500_000    # 触发 rotate 时保留尾部 ~500 KB


def _rotate_log_if_oversized() -> None:
    """日志超过 _LOG_MAX_BYTES → 保留尾部 _LOG_KEEP_BYTES,丢前面。

    实现:读 → 切片 → 找首个完整行 → 原子写。失败仅 warning。
    """
    try:
        if not LOG_PATH.exists() or LOG_PATH.stat().st_size <= _LOG_MAX_BYTES:
            return
        data = LOG_PATH.read_bytes()
        tail = data[-_LOG_KEEP_BYTES:]
        # 找第一个换行,从下一行开始(避免行被切两半)
        nl = tail.find(b"\n")
        if nl != -1:
            tail = tail[nl + 1:]
        tmp = LOG_PATH.with_suffix(LOG_PATH.suffix + ".tmp")
        tmp.write_bytes(tail)
        tmp.replace(LOG_PATH)
        logger.info(
            "subject.log_rotated old_size=%d kept_size=%d",
            len(data), len(tail),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("subject.log_rotate_failed err=%r", exc)


def _log_generation(
    *,
    today_iso: str,
    data: SubjectData,
    llm_raw: str | None,
    final_subject: str,
    fallback_layer: str,  # "llm_pass1" / "llm_pass2" / "static_fallback" / "cache"
) -> None:
    """每次生成追加一行 JSON 日志,便于事后审查 prompt 漂移。

    超过 _LOG_MAX_BYTES 时自动 rotate(保留尾部),避免 GH cache 膨胀。
    """
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _rotate_log_if_oversized()
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
        # 主题只有 8 个汉字；显式关闭思考，避免推理占满预算或撞 10s 超时。
        max_tokens=256,
        temperature=0.95,     # 用户要求更多样性(原 0.85 → 0.95)
        top_p=0.9,            # plan 要求
        timeout=timeout,
        thinking=False,
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
    import re
    s = raw.strip()
    # 去常见包裹符号
    for pair in [('"', '"'), ("'", "'"), ('"', '"'), ("「", "」"), ("『", "』")]:
        if s.startswith(pair[0]) and s.endswith(pair[1]):
            s = s[1:-1].strip()
    # 去常见前缀
    for prefix in ["主题:", "主题:", "今日:", "输出:", "输出:"]:
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
    # 半角空格 → 全角(若中间出现半角空格,救一下;CJK 之间多半是全角分隔)。
    # 用单步 re.sub 收敛任意连续空白:之前的双 .replace 在 "a   b"(三连空格)等
    # 输入下会产出 "a　　b"(两个全角空格,len 长 1),触发 validator 拒绝。
    if " " in s and "　" not in s:
        s = re.sub(r"\s+", "　", s)
    return s
