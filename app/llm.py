"""Small client for OpenAI-compatible chat APIs (OpenAI, Ollama, LM Studio, Groq, ...)."""

import json
import logging
import re
import uuid

import httpx

from app.config import settings
from app.redact import redact

log = logging.getLogger("clawmind.llm")

THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, base_url, api_key, model, embedding_model="", timeout=90, transport=None):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.embedding_model = embedding_model or ""
        self.timeout = timeout
        self.transport = transport

    @property
    def available(self):
        return bool(self.base_url and self.model)

    @property
    def can_embed(self):
        return self.available and bool(self.embedding_model)

    def _post(self, path, payload):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                resp = client.post(self.base_url + path, json=payload, headers=headers)
        except httpx.TimeoutException:
            raise LLMError("The AI model took too long to respond.") from None
        except httpx.HTTPError as exc:
            log.warning("LLM request failed: %s", type(exc).__name__)
            raise LLMError("Could not connect to the AI model. Check LLM_BASE_URL and that the server is running.") from None

        if resp.status_code in (401, 403):
            raise LLMError("The AI provider rejected the request. Check LLM_API_KEY.")
        if resp.status_code == 404:
            raise LLMError("The AI provider returned 404. Check LLM_BASE_URL and LLM_MODEL.")
        if resp.status_code == 429:
            raise LLMError("The AI provider is rate limiting requests. Try again in a moment.")
        if resp.status_code >= 400:
            log.warning("LLM error %s: %s", resp.status_code, redact(resp.text[:300]))
            raise LLMError(f"The AI provider returned an error (HTTP {resp.status_code}).")

        try:
            return resp.json()
        except ValueError:
            raise LLMError("The AI provider sent a response that was not valid JSON.") from None

    def chat(self, messages, tools=None, temperature=0.2):
        """Send messages and return {"content": str, "tool_calls": [{"id", "name", "arguments"}]}."""
        if not self.available:
            raise LLMError("No AI model is configured.")

        payload = {"model": self.model, "messages": messages, "temperature": temperature}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        data = self._post("/chat/completions", payload)
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("The AI provider sent an unexpected response.") from None

        content = message.get("content") or ""
        content = THINK_BLOCK.sub("", content).strip()

        calls = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError:
                    args = {"_invalid_json": args[:200]}
            calls.append({
                "id": raw.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                "name": fn.get("name", ""),
                "arguments": args,
            })

        return {"content": content, "tool_calls": calls}

    def embed(self, texts):
        if not self.can_embed:
            raise LLMError("No embedding model is configured.")
        data = self._post("/embeddings", {"model": self.embedding_model, "input": texts})
        try:
            return [item["embedding"] for item in data["data"]]
        except (KeyError, TypeError):
            raise LLMError("The embedding endpoint sent an unexpected response.") from None


def get_llm():
    return LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        embedding_model=settings.embedding_model,
        timeout=settings.llm_timeout,
    )
