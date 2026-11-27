from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from companion.server.app.database import Base


class AssetPackageModel(Base):
    """4D：已启用的素材包（同一时刻最多一条 active、一条 previous）。

    `storage_path` 只存 `DATA_DIR/assets` 下的目录名，帧文件为
    `frames/000.png` ...，顺序与 manifest 的 `frames` 一致。
    """

    __tablename__ = "asset_packages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    fps: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    frame_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    storage_path: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    activated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
