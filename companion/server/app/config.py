from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
import secrets

class Settings(BaseSettings):
    APP_NAME: str = "RikkaHub Companion Server"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    
    # 服务器绑定
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    
    # 数据持久化目录与数据库路径
    DATA_DIR: Path = Path(__file__).resolve().parent.parent / "data"
    DATABASE_NAME: str = "companion.db"
    
    # 安全与鉴权
    SECRET_KEY: str = secrets.token_hex(32)
    ACCESS_TOKEN_EXPIRE_DAYS: int = 30
    ADMIN_PASSWORD: str = ""  # 留空时首次启动由用户设置，或通过命令行设置
    
    # 外部云模型密钥 (优先从环境变量读取，或通过网页端设置存入数据库)
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_DEFAULT_MODEL: str = "deepseek-chat"
    
    # 语音云服务 (阿里云百炼)
    DASHSCOPE_API_KEY: str = ""
    DASHSCOPE_ASR_MODEL: str = "fun-asr-realtime"
    DASHSCOPE_TTS_MODEL: str = "qwen-audio-3.0-tts-flash"
    DASHSCOPE_TTS_VOICE: str = "cherry"  # 默认温柔男声预设
    
    # 限制与配额 (防 Zip 炸弹和滥用)
    MAX_UPLOAD_SIZE_BYTES: int = 100 * 1024 * 1024  # 100 MB
    MAX_ZIP_EXTRACTED_BYTES: int = 500 * 1024 * 1024  # 500 MB
    
    @property
    def database_url(self) -> str:
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        db_path = self.DATA_DIR / self.DATABASE_NAME
        return f"sqlite+aiosqlite:///{db_path.as_posix()}"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
