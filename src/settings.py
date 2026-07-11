"""
环境变量读取(本地 .env / 生产 GitHub Secrets)。

只声明当前里程碑用得到的字段;未来 M3/M4 接入新数据源时再追加。
缺字段时启动失败(pydantic-settings 默认行为)——比静默用空 KEY 更好。
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EmailSettings(BaseSettings):
    """仅邮件发送所需配置，供主流程与独立监控复用。"""

    # M2:邮件发送
    qq_email_address: str = Field(..., description="QQ 邮箱地址,作为 SMTP 发件方")
    qq_email_auth_code: str = Field(..., description="QQ 邮箱 16 位授权码,非登录密码")
    email_recipient: str = Field(..., description="收件邮箱;逗号分隔可填多个,如 a@qq.com,b@gmail.com")

    @field_validator("email_recipient")
    @classmethod
    def _email_recipient_nonempty(cls, v: str) -> str:
        """空字符串 / 全是逗号空白 → 启动失败。
        否则会浪费完整采集 + LLM 流程,SMTP 接到空 recipients 静默 success → 漏发。"""
        cleaned = [p.strip() for p in (v or "").split(",") if p.strip()]
        if not cleaned:
            raise ValueError(
                "EMAIL_RECIPIENT 必须配置至少一个收件邮箱(逗号分隔可多个)"
            )
        return v
    @field_validator("qq_email_address", "qq_email_auth_code")
    @classmethod
    def _nonempty(cls, v: str, info) -> str:
        if not (v or "").strip():
            raise ValueError(f"{info.field_name} 必须配置非空值")
        return v

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


class Settings(EmailSettings):
    """主流程全部配置。命名与 GitHub Secrets / .env 大小写不敏感对应。"""

    # M3:数据采集层
    finnhub_api_key: str = Field(..., description="Finnhub 免费 API Key,用于持仓公司新闻")
    fred_api_key: str = Field(..., description="FRED 免费 API Key,用于宏观情绪指标")

    # M3 补丁(用户要求中文标题):DeepSeek 调用用于标题翻译;M4 起 processors/ 接管
    deepseek_api_key: str = Field(..., description="DeepSeek API Key")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # 忽略 .env 中其他未声明字段(M1 阶段填入的 DEEPSEEK / FINNHUB 等)
    )


def load_settings() -> Settings:
    """从环境读取并构造 Settings;失败将抛 ValidationError,启动即可见错。"""
    return Settings()  # type: ignore[call-arg]


def load_email_settings() -> EmailSettings:
    """只读取告警邮件需要的环境变量，不要求行情或 LLM API 密钥。"""
    return EmailSettings()  # type: ignore[call-arg]
