import ipaddress
import urllib.parse
import httpx
from bs4 import BeautifulSoup
import logging

logger = logging.getLogger(__name__)

class SearchProvider:
    """
    轻量联网搜索适配器，优先抓取 Bing 搜索结果摘要，具备 SSRF 防御
    """
    @staticmethod
    def is_safe_url(url: str) -> bool:
        try:
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return False
            host = parsed.hostname
            if not host:
                return False
            # 阻止局域网和回环地址 (SSRF 防御)
            try:
                ip = ipaddress.ip_address(host)
                if ip.is_private or ip.is_loopback or ip.is_reserved:
                    return False
            except ValueError:
                # 域名，进一步排除 localhost 和私有顶级域
                if host in ("localhost", "127.0.0.1", "0.0.0.0") or host.endswith(".local"):
                    return False
            return True
        except Exception:
            return False

    @classmethod
    async def search(cls, query: str, limit: int = 4) -> list[dict[str, str]]:
        """
        发起搜索并返回结果列表: [{"title": "...", "snippet": "...", "url": "..."}]
        """
        if not query.strip():
            return []

        search_url = f"https://cn.bing.com/search?q={urllib.parse.quote(query)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        results = []
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                resp = await client.get(search_url, headers=headers)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    items = soup.select(".b_algo")
                    for item in items[:limit]:
                        title_elem = item.select_one("h2 a")
                        snippet_elem = item.select_one(".b_caption p, .b_algoSlug")
                        if title_elem:
                            title = title_elem.get_text(strip=True)
                            href = title_elem.get("href", "")
                            snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""
                            if cls.is_safe_url(href):
                                results.append({
                                    "title": title,
                                    "snippet": snippet,
                                    "url": href
                                })
        except Exception as e:
            logger.warning(f"搜索请求失败 ({query}): {e}")

        return results
