"""Minimal NVIDIA NIM client (OpenAI-compatible REST). Works against the hosted API catalog
(integrate.api.nvidia.com) or a self-hosted NIM container. Standard library only."""
from __future__ import annotations

import json
import os
import urllib.request


class NIMClient:
    def __init__(self, api_key: str | None, base_url: str, chat_model: str, embed_model: str, timeout: int = 60):
        self.api_key, self.base_url = api_key, base_url.rstrip("/")
        self.chat_model, self.embed_model, self.timeout = chat_model, embed_model, timeout

    @classmethod
    def from_env(cls) -> "NIMClient | None":
        key, base = os.getenv("NVIDIA_API_KEY"), os.getenv("NIM_BASE_URL")
        if not key and not base:
            return None  # offline mode
        return cls(key, base or "https://integrate.api.nvidia.com/v1",
                   os.getenv("NIM_CHAT_MODEL", "meta/llama-3.3-70b-instruct"),
                   os.getenv("NIM_EMBED_MODEL", "nvidia/nv-embedqa-e5-v5"))

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(f"{self.base_url}{path}", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", "Accept": "application/json",
                                              **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def chat(self, messages: list[dict], temperature: float = 0.0, max_tokens: int = 1500) -> str:
        out = self._post("/chat/completions", {"model": self.chat_model, "messages": messages,
                                               "temperature": temperature, "max_tokens": max_tokens})
        return out["choices"][0]["message"]["content"]

    def embed(self, texts: list[str], input_type: str = "passage") -> list[list[float]]:
        out = self._post("/embeddings", {"model": self.embed_model, "input": texts,
                                         "input_type": input_type, "encoding_format": "float",
                                         "truncate": "END"})
        return [d["embedding"] for d in sorted(out["data"], key=lambda d: d["index"])]
