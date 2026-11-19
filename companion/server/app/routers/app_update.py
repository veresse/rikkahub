import logging
import httpx
from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/app-update", tags=["app-update"])

# 你的专属 GitHub 仓库名
GITHUB_REPO = "veresse/rikkahub"

class UpdateDownload(BaseModel):
    name: str
    url: str
    size: str

class UpdateInfo(BaseModel):
    version: str
    publishedAt: str
    changelog: str
    downloads: list[UpdateDownload]

@router.get("", response_model=UpdateInfo)
async def check_app_update():
    """
    为手机端 RikkaHub 提供专属更新检查服务。
    自动拉取并适配 veresse/rikkahub 的 GitHub Latest Release。
    手机端在无代理环境下可直接通过伴侣服务器获取更新信息。
    """
    github_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "RikkaHub-Companion-Server"
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(github_url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                tag = data.get("tag_name", "3.2.1")
                # 剔除开头的 'v' 或 'V'
                clean_version = tag.lstrip("vV")
                published_at = data.get("published_at", "")
                changelog = data.get("body", "") or "夏以昼伴侣专属更新版本"

                downloads = []
                for asset in data.get("assets", []):
                    name = asset.get("name", "")
                    if name.endswith(".apk"):
                        size_mb = round(asset.get("size", 0) / (1024 * 1024), 1)
                        downloads.append(UpdateDownload(
                            name=name,
                            url=asset.get("browser_download_url", ""),
                            size=f"{size_mb} MB"
                        ))

                if downloads:
                    return UpdateInfo(
                        version=clean_version,
                        publishedAt=published_at,
                        changelog=changelog,
                        downloads=downloads
                    )
    except Exception as e:
        logger.warning(f"获取 GitHub 最新 Release 失败: {e}")

    # 兜底返回 (当暂无 Release 时保持兼容)
    return UpdateInfo(
        version="3.2.1",
        publishedAt="2026-11-19T20:00:00Z",
        changelog="当前已是最新专属增强版",
        downloads=[]
    )
