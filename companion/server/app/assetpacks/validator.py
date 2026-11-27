"""素材包 ZIP 校验（最小可用规范）。

只认 `manifest.json` + `frames/<3+ 位数字>.png`。所有失败都抛
`AssetValidationError(code)`，由 API 层转成 400 与固定错误码，
不把 zip 内的用户内容带进响应或日志。
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import zipfile
import zlib
from pathlib import Path
from typing import Any

from companion.server.app.config import settings

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MANIFEST_MAX_BYTES = 16 * 1024
_MAX_ENTRIES = 256
_FRAME_RE = re.compile(r"^frames/[0-9]{3,}\.png$")
_VERSION_RE = re.compile(r"^[0-9A-Za-z._-]{1,32}$")


class AssetValidationError(Exception):
    """素材包不合法；`code` 是给 App 的稳定错误码。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise AssetValidationError(code)


def _check_entry_name(name: str) -> None:
    """拒绝 zip-slip / 绝对路径 / 反斜杠；目录条目也不接受。"""
    if not name or name.endswith("/"):
        _fail("unsupported_entry")
    if "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        _fail("unsafe_path")
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        _fail("unsafe_path")


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _png_header(data: bytes) -> tuple[int, int, int, int]:
    """解析 PNG 签名 + IHDR；返回 (width, height, bit_depth, color_type)。"""
    if len(data) < 33 or data[:8] != _PNG_SIGNATURE:
        _fail("invalid_frame")
    ihdr_length = struct.unpack(">I", data[8:12])[0]
    if ihdr_length != 13 or data[12:16] != b"IHDR":
        _fail("invalid_frame")
    width, height = struct.unpack(">II", data[16:24])
    bit_depth = data[24]
    color_type = data[25]
    return width, height, bit_depth, color_type


def _parse_manifest(raw: bytes) -> dict[str, Any]:
    if len(raw) > _MANIFEST_MAX_BYTES:
        _fail("invalid_manifest")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("invalid_manifest")
    if not isinstance(manifest, dict):
        _fail("invalid_manifest")

    name = manifest.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64:
        _fail("invalid_manifest")
    version = manifest.get("version")
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        _fail("invalid_manifest")
    fps = manifest.get("fps")
    if isinstance(fps, bool) or not isinstance(fps, int) or not 1 <= fps <= 60:
        _fail("invalid_manifest")
    frames = manifest.get("frames")
    if not isinstance(frames, list) or not frames:
        _fail("invalid_manifest")
    if len(frames) > settings.ASSET_MAX_FRAMES:
        _fail("too_many_frames")
    # 先确认每个元素都是字符串，再 `set()` 去重：不可哈希的元素（dict/list）
    # 会在 `set(frames)` 里抛 TypeError，被 API 层漏成 500。
    for path in frames:
        if not isinstance(path, str) or not _FRAME_RE.fullmatch(path):
            _fail("invalid_manifest")
    if len(set(frames)) != len(frames):
        _fail("invalid_manifest")

    return {
        "name": name.strip(),
        "version": version,
        "fps": fps,
        "frames": list(frames),
    }


def validate_asset_zip(zip_path: Path, dest_dir: Path) -> dict[str, Any]:
    """校验并展开素材包；成功返回元数据，失败抛 [AssetValidationError]。

    帧文件会被重写成 `frames/{index:03d}.png`（索引顺序 = manifest 顺序），
    方便按整数索引直接读取。
    """
    try:
        archive_size = zip_path.stat().st_size
    except OSError:
        _fail("invalid_archive")
    if archive_size > settings.ASSET_MAX_ARCHIVE_BYTES:
        _fail("archive_too_large")

    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        _fail("invalid_archive")

    with zf:
        infos = zf.infolist()
        if len(infos) > _MAX_ENTRIES:
            _fail("too_many_entries")

        names: list[str] = []
        seen: set[str] = set()
        for info in infos:
            name = info.filename
            _check_entry_name(name)
            if _is_symlink(info):
                _fail("unsafe_path")
            lowered = name.lower()
            if lowered in seen:
                _fail("duplicate_entry")
            seen.add(lowered)
            names.append(name)

        if "manifest.json" not in names:
            _fail("missing_manifest")

        if sum(info.file_size for info in infos) > settings.ASSET_MAX_EXTRACTED_BYTES:
            _fail("extracted_too_large")

        try:
            manifest_raw = zf.read("manifest.json")
        except (KeyError, zipfile.BadZipFile, RuntimeError, zlib.error, OSError):
            _fail("invalid_archive")
        manifest = _parse_manifest(manifest_raw)

        frames = manifest["frames"]
        allowed = {"manifest.json", *frames}
        if any(name not in allowed for name in names):
            _fail("unsupported_entry")
        for path in frames:
            if path not in names:
                _fail("invalid_manifest")

        frames_dir = dest_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        frame_records: list[dict[str, Any]] = []
        digest_input = bytearray()
        for index, path in enumerate(frames):
            info = zf.getinfo(path)
            if info.file_size > settings.ASSET_MAX_FRAME_BYTES:
                _fail("frame_too_large")
            try:
                data = zf.read(path)
            except (zipfile.BadZipFile, RuntimeError, zlib.error, OSError):
                # 结构合法但 deflate 数据损坏时 zlib 抛 `zlib.error`；
                # 这是不可信的归档内容，必须收敛成 400 而不是 500。
                _fail("invalid_archive")
            if len(data) > settings.ASSET_MAX_FRAME_BYTES:
                _fail("frame_too_large")

            width, height, bit_depth, color_type = _png_header(data)
            if width != 240 or height != 240 or bit_depth != 8 or color_type not in (2, 6):
                _fail("invalid_frame")

            frame_sha = hashlib.sha256(data).hexdigest()
            canonical = f"frames/{index:03d}.png"
            (frames_dir / f"{index:03d}.png").write_bytes(data)
            frame_records.append({"path": canonical, "sha256": frame_sha})
            digest_input.extend(f"{canonical}:{frame_sha}\n".encode("utf-8"))

        normalized_manifest = {
            "name": manifest["name"],
            "version": manifest["version"],
            "fps": manifest["fps"],
            "frames": [record["path"] for record in frame_records],
        }
        (dest_dir / "manifest.json").write_text(
            json.dumps(normalized_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        package_sha = hashlib.sha256(bytes(digest_input)).hexdigest()
        (dest_dir / "integrity.json").write_text(
            json.dumps(
                {"sha256": package_sha, "frames": frame_records},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        return {
            "name": manifest["name"],
            "version": manifest["version"],
            "fps": manifest["fps"],
            "frame_count": len(frame_records),
            "size_bytes": archive_size,
            "sha256": package_sha,
            "frames": frame_records,
        }
