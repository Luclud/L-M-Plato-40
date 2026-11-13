"""Optional LLM wording (never required, never used for facts or decisions).

Enabled only when a provider and key are configured (``.env`` or environment):

    LLM_PROVIDER=anthropic|openai     LLM_API_KEY=...     LLM_MODEL=...
    LLM_BASE_URL=...  (optional, OpenAI-compatible endpoint)

Temperature 0, on-disk cache in ``.llm_cache/`` keyed by a hash of the request, short timeout.
Any problem (no config, network error, bad answer) returns ``None`` and the caller keeps its
template. After the first network error the client disables itself for the rest of the run so
a broken key never slows the replay down. ``set_enabled(False)`` (``run.py --no-llm``) or
``PLATO40_NO_LLM=1`` forces templates.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / ".llm_cache"
TIMEOUT_S = 15

_forced_off = False
_broken = False
_env_cache: dict | None = None


def set_enabled(flag: bool) -> None:
    """Globally allow (default) or forbid LLM calls for this process."""
    global _forced_off
    _forced_off = not flag


def _dotenv() -> dict:
    global _env_cache
    if _env_cache is None:
        vals: dict[str, str] = {}
        for p in (Path.cwd() / ".env", ROOT / ".env"):
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = re.sub(r"(^|\s+)#.*$", "", v)          # inline comment
                vals.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        _env_cache = vals
    return _env_cache


def _cfg(key: str) -> str:
    return (os.environ.get(key) or _dotenv().get(key) or "").strip()


def config() -> dict | None:
    """Provider config, or None when the LLM is disabled / not configured."""
    if _forced_off or _broken or os.environ.get("PLATO40_NO_LLM", "") not in ("", "0"):
        return None
    provider, key, model = _cfg("LLM_PROVIDER").lower(), _cfg("LLM_API_KEY"), _cfg("LLM_MODEL")
    if provider not in ("anthropic", "openai") or not key or not model:
        return None
    return {"provider": provider, "key": key, "model": model, "base_url": _cfg("LLM_BASE_URL")}


def enabled() -> bool:
    return config() is not None


def _post(url: str, headers: dict, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 (configured endpoint)
        return json.loads(r.read().decode())


def _call(cfg: dict, system: str, prompt: str, max_tokens: int) -> str:
    if cfg["provider"] == "anthropic":
        url = (cfg["base_url"] or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
        out = _post(url, {"x-api-key": cfg["key"], "anthropic-version": "2023-06-01"},
                    {"model": cfg["model"], "max_tokens": max_tokens, "temperature": 0,
                     "system": system, "messages": [{"role": "user", "content": prompt}]})
        return "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
    url = (cfg["base_url"] or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    out = _post(url, {"authorization": f"Bearer {cfg['key']}"},
                {"model": cfg["model"], "temperature": 0, "max_tokens": max_tokens,
                 "messages": [{"role": "system", "content": system},
                              {"role": "user", "content": prompt}]})
    return out["choices"][0]["message"]["content"]


def complete(system: str, prompt: str, max_tokens: int = 300) -> str | None:
    """Return the model's text (cached) or None. Never raises."""
    global _broken
    cfg = config()
    if cfg is None:
        return None
    key = hashlib.sha256(json.dumps([cfg["provider"], cfg["model"], system, prompt, max_tokens],
                                    ensure_ascii=False).encode()).hexdigest()
    path = CACHE_DIR / f"{key}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))["text"]
    except (OSError, ValueError, KeyError):
        pass
    try:
        text = (_call(cfg, system, prompt, max_tokens) or "").strip()
    except Exception:  # network, auth, quota, malformed response -> template fallback
        _broken = True
        return None
    if not text:
        return None
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return text
