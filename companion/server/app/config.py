import os
import secrets
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_data_dir() -> Path:
    """在 Settings 实例化之前解析数据目录，用于持久化 SECRET_KEY。"""
    return Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


def _load_or_create_secret_key() -> str:
    """SECRET_KEY 必须跨重启稳定，否则已签发的会话/配对状态全部失效。

    优先使用环境变量 SECRET_KEY；否则在 DATA_DIR 下生成 32 字节随机密钥文件，
    权限尽量收紧到 0600（Windows 上可能不生效，但不会降低安全强度）。
    """
    data_dir = _resolve_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    key_path = data_dir / "secret.key"
    if key_path.exists():
        value = key_path.read_text(encoding="utf-8").strip()
        if value:
            return value

    value = secrets.token_hex(32)
    key_path.write_text(value, encoding="utf-8")
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    return value


class Settings(BaseSettings):
    APP_NAME: str = "RikkaHub Companion Server"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = False

    # 服务器绑定
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    # 数据持久化目录与数据库路径
    DATA_DIR: Path = Path(__file__).resolve().parent.parent / "data"
    DATABASE_NAME: str = "companion.db"

    # 迁移：启动时自动执行 alembic upgrade head
    AUTO_MIGRATE: bool = True

    # 安全与鉴权
    SECRET_KEY: str = Field(default_factory=_load_or_create_secret_key)
    ADMIN_USERNAME: str = "admin"
    # 二选一：明文仅用于首次引导（环境变量），或使用 pbkdf2 哈希。
    ADMIN_PASSWORD: str = ""
    ADMIN_PASSWORD_HASH: str = ""
    APP_TOKEN_EXPIRE_DAYS: int = 30
    PAIR_CODE_TTL_SECONDS: int = 300
    PAIR_CODE_MAX_ATTEMPTS: int = 5
    LOGIN_RATE_LIMIT: int = 5
    LOGIN_RATE_WINDOW_SECONDS: int = 300

    # CORS：默认关闭；如确有需要，用 JSON 数组配置白名单，禁止使用 "*"。
    CORS_ALLOW_ORIGINS: list[str] = []

    # 外部云模型密钥 (优先从环境变量读取，不在仓库中保存)
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_DEFAULT_MODEL: str = "deepseek-chat"

    # 语音云服务 (阿里云百炼)
    DASHSCOPE_API_KEY: str = ""
    DASHSCOPE_ASR_MODEL: str = "fun-asr-realtime"
    DASHSCOPE_TTS_MODEL: str = "qwen-audio-3.0-tts-flash"
    DASHSCOPE_TTS_VOICE: str = "cherry"  # 默认温柔男声预设
    # 下行 TTS 帧长；与设备 hello 请求不同时以本值为准并附 warning。
    VOICE_DOWNLINK_FRAME_MS: int = 60

    # 动作编排限制
    MAX_ACTIONS_PER_TURN: int = 3
    ACTION_RESULT_TIMEOUT_EXTRA_MS: int = 1500

    # WebSocket 网关
    DEVICE_HELLO_TIMEOUT_SECONDS: float = 5.0
    DEVICE_STATUS_TIMEOUT_SECONDS: float = 3.0
    WS_MAX_MESSAGE_BYTES: int = 64 * 1024
    # 传输层上限必须高于应用层阈值，否则超限帧会先被 uvicorn 以 1009 关闭，
    # 应用层的 4429 message_too_large 分支永远不可达。
    WS_TRANSPORT_MAX_MESSAGE_BYTES: int = 128 * 1024
    WS_PING_INTERVAL_SECONDS: float = 25.0
    WS_PING_TIMEOUT_SECONDS: float = 10.0

    # 限制与配额 (防 Zip 炸弹和滥用)
    MAX_UPLOAD_SIZE_BYTES: int = 100 * 1024 * 1024  # 100 MB
    MAX_ZIP_EXTRACTED_BYTES: int = 500 * 1024 * 1024  # 500 MB

    @property
    def database_url(self) -> str:
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        db_path = self.DATA_DIR / self.DATABASE_NAME
        return f"sqlite+aiosqlite:///{db_path.as_posix()}"

    @property
    def sync_database_url(self) -> str:
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        db_path = self.DATA_DIR / self.DATABASE_NAME
        return f"sqlite:///{db_path.as_posix()}"

    @model_validator(mode="after")
    def _validate_ws_limits(self):
        # uvicorn 必须先放过超限帧，应用层才能用自定义 4429 关闭。
        if self.WS_TRANSPORT_MAX_MESSAGE_BYTES <= self.WS_MAX_MESSAGE_BYTES:
            raise ValueError(
                "WS_TRANSPORT_MAX_MESSAGE_BYTES must be greater than "
                "WS_MAX_MESSAGE_BYTES"
            )
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
