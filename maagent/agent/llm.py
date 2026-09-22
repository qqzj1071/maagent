from __future__ import annotations

import base64
import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from loguru import logger


class LLMError(RuntimeError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def image_data_url(image: Any) -> str:
    """Turn a path / bytes / PIL image into an OpenAI-style data URL."""
    if isinstance(image, (str, Path)):
        data = Path(image).read_bytes()
        mime = "image/png" if str(image).lower().endswith(".png") else "image/jpeg"
    elif isinstance(image, (bytes, bytearray)):
        data = bytes(image)
        mime = "image/png"
    else:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        data = buf.getvalue()
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


class OpenAICompatLLM:
    """Minimal OpenAI-compatible chat client (works with 智谱 GLM, OpenRouter, ...)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        vision_model: str | None = None,
        temperature: float = 0.7,
        timeout: float = 90.0,
        max_retries: int = 6,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.vision_model = vision_model or model
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries

    def _retry_delay(self, attempt: int, resp: requests.Response | None) -> float:
        if resp is not None:
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    return max(1.0, float(retry_after))
                except ValueError:
                    pass
        return min(2.0 ** attempt, 30.0)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = self.base_url + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            resp: requests.Response | None = None
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            except requests.RequestException as e:
                last = e
                delay = self._retry_delay(attempt, None)
                logger.warning("LLM 请求失败（第 {} 次），{}s 后重试: {}", attempt, delay, e)
                time.sleep(delay)
                continue
            if resp.status_code == 200:
                return resp.json()
            detail = resp.text[:300]
            if resp.status_code in (408, 409, 425, 429, 500, 502, 503, 504):
                last = LLMError(f"HTTP {resp.status_code}: {detail}")
                delay = self._retry_delay(attempt, resp)
                logger.warning("LLM 返回 {}（第 {} 次），{}s 后重试", resp.status_code, attempt, delay)
                time.sleep(delay)
                continue
            raise LLMError(f"HTTP {resp.status_code}: {detail}")
        raise LLMError(f"LLM 请求失败: {last}")

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> LLMReply:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        data = self._post(payload)
        choices = data.get("choices") or [{}]
        message = choices[0].get("message") or {}
        calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments")
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args or "{}")
                except json.JSONDecodeError:
                    args = {}
            else:
                args = raw_args or {}
            calls.append(ToolCall(str(tc.get("id") or ""), str(fn.get("name") or ""), args))
        return LLMReply(content=message.get("content") or "", tool_calls=calls, raw=data)
