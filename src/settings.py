"""
环境变量读取(本地 .env / 生产 GitHub Secrets)。

只声明当前里程碑用得到的字段;未来 M3/M4 接入新数据源时再追加。
缺字段时启动失败(pydantic-settings 默认行为)——比静默用空 KEY 更好。
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全部环境变量集中读取。命名与 GitHub Secrets / .env 大小写不敏感对应。"""

    # M2:邮件发送
    qq_email_address: str = Field(..., description="QQ 邮箱地址,作为 SMTP 发件方")
    qq_email_auth_code: str = Field(..., description="QQ 邮箱 16 位授权码,非登录密码")
    email_recipient: str = Field(..., description="收件邮箱(通常等于 qq_email_address)")

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
