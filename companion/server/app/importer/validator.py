import zipfile
from pathlib import Path

# 默认安全常量 (若外部传入则覆盖)
DEFAULT_MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB
DEFAULT_MAX_EXTRACTED_SIZE = 500 * 1024 * 1024  # 500 MB

class ValidationError(Exception):
    pass

def validate_and_extract_backup_zip(
    zip_path: Path, 
    extract_dir: Path,
    max_upload_size: int = DEFAULT_MAX_UPLOAD_SIZE,
    max_extracted_size: int = DEFAULT_MAX_EXTRACTED_SIZE
) -> dict:
    """
    严格校验 ZIP 压缩包安全性，防御 Zip-Slip 路径穿越攻击和 Zip 炸弹，
    并提取 settings.json 和 rikka_hub.db。
    """
    if not zip_path.exists():
        raise ValidationError(f"文件不存在: {zip_path}")

    # 1. 检查物理压缩包大小
    file_size = zip_path.stat().st_size
    if file_size > max_upload_size:
        raise ValidationError(f"备份文件过大 ({file_size} 字节)，超过上限 {max_upload_size} 字节")

    extract_dir.mkdir(parents=True, exist_ok=True)
    resolved_dest = extract_dir.resolve()

    total_extracted_size = 0
    extracted_files = {}

    with zipfile.ZipFile(zip_path, 'r') as zf:
        infolist = zf.infolist()
        
        # 2. 检查总解压大小预估
        for info in infolist:
            total_extracted_size += info.file_size
            if total_extracted_size > max_extracted_size:
                raise ValidationError("解压体积过大，可能包含压缩炸弹，拒绝处理")

            # 3. 防御 Zip-Slip 路径穿越
            target_path = (resolved_dest / info.filename).resolve()
            if not target_path.is_relative_to(resolved_dest):
                raise ValidationError(f"检测到非法文件路径穿越尝试: {info.filename}")

        # 4. 执行安全解压
        zf.extractall(resolved_dest)

    # 5. 校验核心必需文件
    settings_file = resolved_dest / "settings.json"
    db_file = resolved_dest / "rikka_hub.db"

    extracted_files["settings_path"] = settings_file if settings_file.exists() else None
    extracted_files["db_path"] = db_file if db_file.exists() else None

    if not extracted_files["settings_path"] and not extracted_files["db_path"]:
        raise ValidationError("备份文件中未找到 settings.json 或 rikka_hub.db，非合规的 RikkaHub 备份文件")

    return extracted_files
