from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "agent.db"
SECRETS_PATH = DATA_DIR / "secrets.json"
MEDIA_DIR = DATA_DIR / "media"
DEBUG_DIR = DATA_DIR / "debug"
PROFILE_DIR = DATA_DIR / "chrome-profile"
DOWNLOAD_DIR = DATA_DIR / "downloads"
LOG_DIR = DATA_DIR / "logs"
WEB_DIST = ROOT / "web" / "dist"


def ensure_dirs() -> None:
    for path in (DATA_DIR, MEDIA_DIR, DEBUG_DIR, PROFILE_DIR, DOWNLOAD_DIR, LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)


class Env(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    deepseek_api_key: str = ""
    host: str = "127.0.0.1"
    port: int = 8787
    chrome_channel: str = "chrome"
    headless: bool = False
    mock_url: str = ""


env = Env()


class AgentSettings(BaseModel):
    """Everything the operator can tune. Secrets never live in this model."""

    dry_run: bool = True
    auto_like: bool = True
    auto_comment: bool = True
    auto_dm: bool = True
    auto_publish: bool = False
    publish_require_approval: bool = True
    auto_operate: bool = False
    auto_content: bool = True
    auto_video: bool = True
    content_generate_time: str = "07:30"
    publish_time: str = "18:00"

    max_videos_per_run: int = Field(default=8, ge=1, le=30)
    likes_per_day: int = Field(default=30, ge=0, le=300)
    comments_per_day: int = Field(default=8, ge=0, le=80)
    dms_per_day: int = Field(default=3, ge=0, le=30)
    posts_per_day: int = Field(default=0, ge=0, le=10)
    min_gap_sec: int = Field(default=12, ge=3, le=300)

    max_comment_chars: int = Field(default=90, ge=10, le=300)
    comment_mention: bool = False
    max_dm_chars: int = Field(default=160, ge=10, le=500)
    dm_per_contact_per_day: int = Field(default=1, ge=1, le=5)
    dm_allow_mutual: bool = True

    auto_reply: bool = False
    auto_reply_dms: bool = True
    auto_reply_comments: bool = True
    reply_interval_sec: int = Field(default=60, ge=20, le=600)
    dm_reply_scope: Literal["mutual", "existing", "allowlist"] = "existing"
    share_target: str = ""
    share_topics: str = "有趣、有用、值得看"
    auto_share: bool = False
    share_time_1: str = "12:00"
    share_time_2: str = "20:00"
    share_max_per_run: int = Field(default=2, ge=1, le=5)
    share_min_score: float = Field(default=0.7, ge=0, le=1)
    silent_browser: bool = True

    agent_name: str = Field(default="", max_length=40)
    persona: str = (
        "克制、具体、像真人。不要恭维，不要堆网络烂梗，不要推销，"
        "不透露个人隐私，不替任何人做承诺。评论最多两行。"
    )
    topics: str = "科技、知识、社会观察、真实经验"
    content_pillars: str = "经验分享、工具测评、生活记录"
    crm_auto_tag: bool = True
    dm_allowlist: list[str] = Field(default_factory=list)
    comment_blocklist: list[str] = Field(
        default_factory=lambda: [
            "微信", "加微", "加V", "私信我", "二维码", "下单", "优惠",
            "链接", "手机号", "http", "www.", "vx", "VX",
        ]
    )

    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com/v1"
    temperature: float = Field(default=0.7, ge=0, le=1.5)

    feed_url: str = "https://www.douyin.com/?recommend=1&from_nav=1"
    media_dir: str = ""
    auto_interval_min: int = Field(default=0, ge=0, le=1440)
    quiet_hours: str = ""
    updated_at: str = ""


DEFAULT_COMMENT_BLOCKLIST = list(AgentSettings().comment_blocklist)


def settings_public(settings: AgentSettings, has_api_key: bool) -> dict[str, Any]:
    data = settings.model_dump()
    data["has_api_key"] = has_api_key
    return data


def load_api_key() -> str:
    if env.deepseek_api_key.strip():
        return env.deepseek_api_key.strip()
    if SECRETS_PATH.exists():
        try:
            data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
            key = str(data.get("deepseek_api_key") or "").strip()
            if key:
                return key
        except (OSError, ValueError):
            pass
    return ""


def save_api_key(key: str) -> None:
    ensure_dirs()
    payload = {"deepseek_api_key": key.strip()}
    SECRETS_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(SECRETS_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def clear_api_key() -> None:
    if SECRETS_PATH.exists():
        SECRETS_PATH.unlink()
