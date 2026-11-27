"""4D：素材包（240×240 PNG 序列）的校验、存储与启用。

包格式见设计 §2.5：只允许 `manifest.json` 与 `frames/NNN.png`。
"""

from companion.server.app.assetpacks.validator import (
    AssetValidationError,
    validate_asset_zip,
)

__all__ = ["AssetValidationError", "validate_asset_zip"]
