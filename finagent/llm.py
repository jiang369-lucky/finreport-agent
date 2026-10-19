import asyncio
import httpx

from .config import settings


class QwenClient:
    def __init__(self):
        settings.validate_model()

    async def complete(self, messages, tools=None):
        if not settings.configured:
            raise ValueError("尚未配置通义千问密钥；请在项目 .env 中设置 DASHSCOPE_API_KEY，或先使用证据检索模式。")
        payload = {"model": settings.model, "messages": messages, "temperature": 0.1,
                   "max_tokens": settings.completion_tokens}
        if tools:
            payload["tools"] = [{"type": "function", "function": tool} for tool in tools]
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = False
        headers = {"Authorization": "Bearer " + (settings.api_key or "local"), "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=settings.request_timeout, trust_env=False) as client:
            for attempt in range(2):
                try:
                    response = await client.post(settings.base_url.rstrip("/") + "/chat/completions", json=payload, headers=headers)
                    if response.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                        await asyncio.sleep(1)
                        continue
                    if response.status_code >= 400:
                        # Do not echo provider payloads or authentication headers into traces.
                        raise ValueError(f"模型服务返回 HTTP {response.status_code}；请核对地域端点、固定模型权限与本地密钥。")
                    result = response.json()
                    message = result["choices"][0]["message"]
                    return {"message": {k: message[k] for k in ("role", "content", "tool_calls") if k in message},
                            "usage": result.get("usage", {})}
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    if attempt:
                        raise ValueError("模型请求超时或网络不可达；本次研究已保留工具记录。") from exc
                    await asyncio.sleep(1)
