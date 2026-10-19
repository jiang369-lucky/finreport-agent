from dataclasses import dataclass
from datetime import date
from pathlib import Path
import os
import re

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if os.getenv("FIN_DATA_PROCESS") != "1":
    load_dotenv(ROOT / ".env")
HARD_CUTOFF = date(2025, 8, 31)
MODEL_RELEASES = {
    "qwen3-235b-a22b-instruct-2507": "2025-07-22",
    "qwen-plus-2025-07-14": "2025-07-14",
    "Qwen/Qwen3-8B": "2025-04-29",
    "qwen3-8b": "2025-04-29",
}


def validate_as_of(value: str | None) -> str:
    parsed = date.fromisoformat(value or settings.cutoff)
    if parsed > HARD_CUTOFF:
        raise ValueError("数据截止日不能晚于 2025-08-31")
    return parsed.isoformat()


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.getenv("FIN_DATA_DIR", str(ROOT / "data")))
    database_url: str = os.getenv("DATABASE_URL", "")
    database_schema: str = os.getenv("FIN_DATABASE_SCHEMA", "finagent")
    cutoff: str = os.getenv("FIN_DATA_CUTOFF", "2025-08-31")
    base_url: str = os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    model: str = os.getenv("LLM_MODEL", "qwen3-235b-a22b-instruct-2507")
    api_key: str = os.getenv("DASHSCOPE_API_KEY", "")
    context_tokens: int = int(os.getenv("FIN_CONTEXT_TOKENS", "12000"))
    max_steps: int = int(os.getenv("FIN_MAX_STEPS", "10"))
    max_tool_calls: int = int(os.getenv("FIN_MAX_TOOL_CALLS", "16"))
    run_timeout: int = int(os.getenv("FIN_RUN_TIMEOUT", "180"))
    completion_tokens: int = int(os.getenv("FIN_MAX_COMPLETION_TOKENS", "1800"))
    request_timeout: int = int(os.getenv("FIN_REQUEST_TIMEOUT", "55"))

    def validate_database(self) -> None:
        if not self.database_url.startswith(("postgresql://", "postgres://")) or "CHANGE_ME" in self.database_url:
            raise ValueError("请先运行 scripts.configure_database 配置 PostgreSQL DATABASE_URL，再启动数据库。")
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}", self.database_schema):
            raise ValueError("数据库 schema 名称无效")

    @property
    def configured(self) -> bool:
        return bool(self.api_key) or self.base_url.startswith(("http://127.0.0.1:", "http://localhost:"))

    def validate_model(self) -> None:
        if self.model not in MODEL_RELEASES:
            raise ValueError("模型未在历史版本白名单中；请使用 .env.example 中的固定模型名")
        if "latest" in self.model:
            raise ValueError("不能使用会滚动更新的 latest 模型")
        validate_as_of(self.cutoff)
        if self.context_tokens < 5000 or self.completion_tokens >= self.context_tokens // 2:
            raise ValueError("上下文预算过小，或输出预算过大")


settings = Settings()
