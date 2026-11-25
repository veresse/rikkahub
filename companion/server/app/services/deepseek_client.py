import json
import httpx
from typing import AsyncGenerator, Any
from companion.server.app.config import settings

class DeepSeekClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or settings.DEEPSEEK_API_KEY
        self.base_url = (base_url or settings.DEEPSEEK_BASE_URL).rstrip("/")

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = 0.7,
        top_p: float | None = 0.9,
        tools: list[dict[str, Any]] | None = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        向 DeepSeek 发起流式请求，逐 token 生成回复。
        产出字典格式：
        - {"type": "content", "delta": "..."}
        - {"type": "reasoning", "delta": "..."} (思考链内容)
        - {"type": "tool_call", "delta": [...]} (OpenAI 流式 tool_calls 增量)
        - {"type": "usage", "usage": {...}}
        - {"type": "done", "finish_reason": "stop|tool_calls"}
        """
        if not self.api_key:
            yield {"type": "error", "error": "DeepSeek API Key 未配置，请在设置中配置"}
            return

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload: dict[str, Any] = {
            "model": model or settings.DEEPSEEK_DEFAULT_MODEL,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True}
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if top_p is not None:
            payload["top_p"] = top_p
        if tools:
            payload["tools"] = tools

        url = f"{self.base_url}/chat/completions"

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                async with client.stream("POST", url, json=payload, headers=headers) as response:
                    if response.status_code != 200:
                        err_text = await response.aread()
                        yield {"type": "error", "error": f"API 错误 ({response.status_code}): {err_text.decode('utf-8', errors='ignore')}"}
                        return

                    done_reason = "stop"
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            yield {"type": "done", "finish_reason": done_reason}
                            break

                        try:
                            chunk = json.loads(data_str)
                            choices = chunk.get("choices", [])
                            if not choices:
                                continue
                            delta = choices[0].get("delta", {})
                            choice_finish = choices[0].get("finish_reason")
                            if choice_finish:
                                done_reason = choice_finish

                            # 思考链提取 (DeepSeek Reasoner 模型会返回 reasoning_content)
                            if "reasoning_content" in delta and delta["reasoning_content"]:
                                yield {"type": "reasoning", "delta": delta["reasoning_content"]}

                            # 正文生成内容
                            if "content" in delta and delta["content"]:
                                yield {"type": "content", "delta": delta["content"]}

                            # 工具调用 (Tool Calls)
                            if "tool_calls" in delta:
                                yield {"type": "tool_call", "delta": delta["tool_calls"]}

                            # 统计信息 (Usage 包含 prompt_cache_hit_tokens)
                            if "usage" in chunk and chunk["usage"]:
                                yield {"type": "usage", "usage": chunk["usage"]}

                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                yield {"type": "error", "error": f"网络请求异常: {str(e)}"}
