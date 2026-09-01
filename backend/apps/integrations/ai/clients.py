"""LLM providers — keys only from settings/env. Never log secrets."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


class AIProviderError(Exception):
    pass


def generate_briefing_text(
    prompt: str,
    *,
    max_tokens: int = 1200,
    allow_wigolo_fallback: bool = True,
    prefer_fast_model: bool = False,
    prefer_long_context: bool = False,
    allow_local_fallback: bool = True,
    retry_rounds: int | None = None,
) -> dict[str, Any]:
    """
    Provider cascade for briefings.

    Paid ShopAIKey is the primary route for both short and detailed reports.
    Existing cloud providers remain fallbacks; Ollama is used only after them.

    prefer_fast_model: try GPT-OSS 20B before 120B (token-saving polish path).
    allow_wigolo_fallback: disable when dossier already came from Wigolo.
    allow_local_fallback: when False (final review), raise instead of the
    "LLM tạm không khả dụng" stub — caller should keep the crawled draft.
    retry_rounds: re-try the full provider chain after short backoff (briefings).
    """
    import time

    errors: list[str] = []
    prompt = (prompt or "").strip()
    rounds = retry_rounds
    if rounds is None:
        rounds = 1
    rounds = max(1, min(int(rounds), 5))

    for attempt in range(rounds):
        if attempt > 0:
            # Give Groq key cooldowns / RPM window a chance to recover.
            time.sleep(min(12.0, 2.5 * attempt))
            logger.info("briefing LLM retry round=%s/%s", attempt + 1, rounds)

        from apps.integrations.ai.cerebras_pool import cerebras_enabled
        from apps.integrations.ai.groq_pool import groq_keys_configured
        from apps.integrations.ai.openrouter_pool import openrouter_enabled
        from apps.integrations.ai.shopaikey_pool import (
            shopaikey_chat_completion,
            shopaikey_enabled,
        )

        def _try_shopaikey() -> dict[str, Any] | None:
            if not (
                bool(getattr(settings, "BRIEFING_SHOPAIKEY_ENABLED", True))
                and shopaikey_enabled()
            ):
                return None
            configured_model = str(
                getattr(settings, "BRIEFING_SHOPAIKEY_MODEL", "qwen3-235b-a22b")
                or "qwen3-235b-a22b"
            ).strip()
            timeout = min(30.0, float(
                getattr(settings, "BRIEFING_SHOPAIKEY_TIMEOUT_SECONDS", 18.0)
                or 18.0
            ))
            paid_prompt_limit = int(
                getattr(settings, "AI_BRIEFING_SHOPAIKEY_PROMPT_CHARS", 22000)
                or 22000
            )
            paid_prompt = _trim_prompt(
                prompt,
                limit=max(8000, min(paid_prompt_limit, 30000)),
            )
            models = (
                ["gpt-5-mini", configured_model, "qwen-flash"]
                if prefer_long_context
                else [configured_model, "qwen-flash"]
            )
            seen_models: set[str] = set()
            for model in models:
                model = str(model or "").strip()
                if not model or model in seen_models:
                    continue
                seen_models.add(model)
                try:
                    result = shopaikey_chat_completion(
                        messages=[
                            {"role": "system", "content": _briefing_system_msg()},
                            {"role": "user", "content": paid_prompt},
                        ],
                        max_tokens=max_tokens,
                        temperature=0.12,
                        model=model,
                        profile="fast" if prefer_fast_model else "deep",
                        timeout=timeout,
                        try_fallback_models=False,
                    )
                    text = str(result.get("text") or "").strip()
                    if not _briefing_provider_text_usable(text):
                        raise AIProviderError("ShopAIKey returned empty briefing text")
                    return {
                        "provider": "shopaikey",
                        "text": text,
                        "raw": {
                            "model": result.get("model") or model,
                            "fallbacks": errors[:8],
                            "paid": True,
                            "prompt_chars": len(paid_prompt),
                        },
                    }
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"shopaikey/{model}: {exc}")
                    logger.warning("briefing ShopAIKey failed model=%s: %s", model, exc)
            return None

        def _try_groq() -> dict[str, Any] | None:
            if not groq_keys_configured(pool="briefing"):
                return None
            models = _groq_briefing_models()
            if prefer_fast_model and len(models) > 1:
                models = list(reversed(models))
            for model in models:
                try:
                    result = groq_complete(prompt, max_tokens=max_tokens, model=model)
                    if not _briefing_provider_text_usable(str(result.get("text") or "")):
                        raise AIProviderError("Groq returned an invalid briefing response")
                    return result
                except AIProviderError as exc:
                    errors.append(f"groq/{model}: {exc}")
                    logger.warning("briefing groq failed model=%s: %s", model, exc)
            return None

        def _try_cerebras() -> dict[str, Any] | None:
            if not (
                cerebras_enabled()
                and bool(getattr(settings, "CEREBRAS_BRIEFING_FALLBACK", True))
            ):
                return None
            try:
                result = cerebras_complete(prompt, max_tokens=max_tokens)
                if not _briefing_provider_text_usable(str(result.get("text") or "")):
                    raise AIProviderError("Cerebras returned an invalid briefing response")
                result["raw"] = {**(result.get("raw") or {}), "fallbacks": errors[:8]}
                return result
            except AIProviderError as exc:
                errors.append(f"cerebras: {exc}")
                logger.warning("briefing cerebras failed: %s", exc)
                return None

        def _try_openrouter() -> dict[str, Any] | None:
            if not openrouter_enabled():
                return None
            try:
                result = openrouter_complete(prompt, max_tokens=max_tokens)
                if not _briefing_provider_text_usable(str(result.get("text") or "")):
                    raise AIProviderError("OpenRouter returned an invalid briefing response")
                result["raw"] = {**(result.get("raw") or {}), "fallbacks": errors[:8]}
                return result
            except AIProviderError as exc:
                errors.append(f"openrouter: {exc}")
                logger.warning("briefing openrouter failed: %s", exc)
                return None

        # Paid route first. Free cloud routes remain ordered by report profile.
        ordered = (
            (_try_shopaikey, _try_groq, _try_openrouter, _try_cerebras)
            if prefer_long_context
            else (_try_shopaikey, _try_groq, _try_cerebras, _try_openrouter)
        )
        for try_fn in ordered:
            hit = try_fn()
            if hit:
                return hit

        try:
            ollama = (
                _ollama_complete(prompt, max_tokens=max_tokens)
                if allow_local_fallback
                else None
            )
            if ollama is None:
                raise AIProviderError("Ollama disabled for this briefing request")
            if ollama.get("text") and not is_local_llm_unavailable_text(
                str(ollama.get("text") or "")
            ):
                ollama["raw"] = {**(ollama.get("raw") or {}), "fallbacks": errors[:8]}
                return ollama
            if ollama.get("text"):
                errors.append("ollama: returned local-stub-like text")
        except AIProviderError as exc:
            errors.append(f"ollama: {exc}")
            logger.warning("briefing ollama failed: %s", exc)

        anthropic_key = getattr(settings, "ANTHROPIC_API_KEY", "") or ""
        if anthropic_key:
            try:
                result = _anthropic_complete(
                    prompt, anthropic_key, max_tokens=max_tokens
                )
                result["raw"] = {**(result.get("raw") or {}), "fallbacks": errors[:8]}
                return result
            except AIProviderError as exc:
                errors.append(f"anthropic: {exc}")

        hf_token = getattr(settings, "HUGGINGFACE_API_TOKEN", "") or ""
        if hf_token:
            model = getattr(
                settings,
                "HUGGINGFACE_SUMMARIZE_MODEL",
                "google/flan-t5-base",
            )
            try:
                result = _huggingface_complete(prompt, hf_token, model=model)
                result["raw"] = {**(result.get("raw") or {}), "fallbacks": errors[:8]}
                return result
            except AIProviderError as exc:
                errors.append(f"huggingface: {exc}")

        if allow_wigolo_fallback:
            try:
                wigolo = _wigolo_briefing_fallback(prompt)
                if wigolo.get("text"):
                    wigolo["raw"] = {
                        **(wigolo.get("raw") or {}),
                        "fallbacks": errors[:8],
                    }
                    return wigolo
            except AIProviderError as exc:
                errors.append(f"wigolo: {exc}")
                logger.warning("briefing wigolo fallback failed: %s", exc)

    if not allow_local_fallback:
        raise AIProviderError(
            "LLM unavailable (no local stub): " + "; ".join(errors[:6])
        )

    return {
        "provider": "local",
        "text": _local_briefing(prompt),
        "raw": {"mode": "local_fallback", "fallbacks": errors[:8]},
    }


def is_local_llm_unavailable_text(text: str) -> bool:
    """True when text is the structured stub used when Groq/Ollama both fail."""
    t = (text or "").casefold()
    return (
        "chế độ local — llm tạm không khả dụng" in t
        or ("che do local" in t and "llm" in t)
        or "groq/ollama không phản hồi" in t
        or "groq/ollama khong phan hoi" in t
        or "llm tạm không khả dụng" in t
    )


def _briefing_provider_text_usable(text: str) -> bool:
    """Reject gateway moderation/status fragments masquerading as completions."""
    value = " ".join((text or "").split()).strip()
    if not value:
        return False
    low = value.casefold().strip(" .:;-")
    if low in {"user safety safe", "safety safe", "safe", "ok"}:
        return False
    return not (
        len(value) < 220
        and ("user safety" in low or "content policy" in low)
    )


def _groq_briefing_models() -> list[str]:
    primary = (
        getattr(settings, "GROQ_MODEL", "openai/gpt-oss-120b")
        or "openai/gpt-oss-120b"
    )
    fallback = (
        getattr(settings, "GROQ_BRIEFING_FALLBACK_MODEL", "openai/gpt-oss-20b")
        or "openai/gpt-oss-20b"
    )
    out: list[str] = []
    for model in (primary, fallback):
        m = str(model).strip()
        if m and m not in out:
            out.append(m)
    return out


def groq_complete(
    prompt: str,
    *,
    max_tokens: int = 400,
    model: str | None = None,
    prompt_limit: int | None = None,
) -> dict[str, Any]:
    """OpenAI-compatible Groq chat completions with multi-key rotation.

    Briefings rotate across several keys on 429/401 so one burned free-tier
    key does not fail the whole summary (title translate stays conservative).
    """
    from apps.integrations.ai.groq_pool import (
        groq_api_keys,
        groq_chat_completion,
        groq_keys_configured,
    )

    if not groq_keys_configured(pool="briefing"):
        raise AIProviderError("GROQ_API_KEYS_BRIEFING is not configured")
    key_n = len(groq_api_keys(pool="briefing"))
    use_model = (
        model
        or getattr(settings, "GROQ_MODEL", "openai/gpt-oss-120b")
        or "openai/gpt-oss-120b"
    )
    # Cap prompt size — Groq free-tier rejects oversized bodies with HTTP 413.
    limit = int(
        prompt_limit
        if prompt_limit is not None
        else (getattr(settings, "AI_BRIEFING_GROQ_PROMPT_CHARS", 10000) or 10000)
    )
    # Leave enough TPM headroom for a detailed 3.2k-token completion. Groq's
    # lower-tier limits can reject a 10k-char prompt even with a 128k context.
    limit = max(3000, min(limit, 8000))
    timeout = min(
        30.0,
        max(8.0, float(
            getattr(settings, "GROQ_BRIEFING_TIMEOUT_SEC", None)
            or getattr(settings, "GROQ_TIMEOUT_SEC", 12)
            or 12
        )),
    )
    system_msg = _briefing_system_msg()
    out_tokens = max(256, min(int(max_tokens or 800), 4000))
    last_exc: Exception | None = None
    # On 413, shrink and retry once — do not burn the key pool.
    for shrink in (limit, max(3000, limit // 2), 3500):
        user_prompt = _trim_prompt(prompt, limit=shrink)
        try:
            result = groq_chat_completion(
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=out_tokens,
                temperature=0.15,
                model=use_model,
                timeout=timeout,
                max_attempts=min(3, max(1, key_n)),
                block_for_budget=True,
                rotate_on_rate_limit=True,
                pool="briefing",
            )
            text = str(result.get("text") or "").strip()
            if not text:
                raise AIProviderError("Groq returned empty briefing text")
            return {
                "provider": "groq",
                "text": text,
                "raw": {
                    "id": result.get("raw_id"),
                    "model": result.get("model"),
                    "key_fp": result.get("key_fp"),
                    "prompt_chars": len(user_prompt),
                },
            }
        except RuntimeError as exc:
            last_exc = exc
            msg = str(exc).casefold()
            if "413" in msg or "too large" in msg or "payload" in msg:
                logger.warning(
                    "groq 413 payload too large — shrink prompt to %s chars",
                    max(3000, shrink // 2) if shrink > 3500 else 3500,
                )
                continue
            raise AIProviderError(str(exc)) from exc
    raise AIProviderError(str(last_exc or "Groq request failed"))


def _briefing_system_msg() -> str:
    return (
        "Bạn là biên tập viên OSINT quốc phòng. "
        "Chỉ viết tiếng Việt rõ ràng, ngắn, bám sát bằng chứng được cung cấp; "
        "không chép câu tiếng Anh từ nguồn vào phần trả lời. "
        "CẤM markdown: không **, không *, không #, không tiêu đề **1. …:**. "
        "Dùng 1) 2) 3) hoặc • ; mỗi mục: ai/cái gì/khi nào/ở đâu theo nguồn. "
        "Ưu tiên trích/paraphrase nguồn; không chắc thì bỏ. "
        "CẤM suy đoán, câu sáo rỗng, phân tích tâm lý không có trong nguồn. "
        "Tuyệt đối không tự đặt tên văn bản, cơ quan, nhân vật, thời điểm, số liệu "
        "hoặc sự kiện. Nếu nguồn không đủ, nêu ngắn gọn là chưa đủ dữ liệu. "
        "Giữ URL https://. Không bịa số liệu."
    )


def cerebras_complete(
    prompt: str,
    *,
    max_tokens: int = 400,
    model: str | None = None,
    prompt_limit: int | None = None,
) -> dict[str, Any]:
    """Cerebras briefing mid-tier (after Groq, before OpenRouter)."""
    from apps.integrations.ai.cerebras_pool import (
        cerebras_chat_completion,
        cerebras_enabled,
    )

    if not cerebras_enabled():
        raise AIProviderError("Cerebras is disabled or has no API keys")
    # Cerebras models support 131k context — allow larger prompts than Groq free.
    limit = int(
        prompt_limit
        if prompt_limit is not None
        else (getattr(settings, "AI_BRIEFING_CEREBRAS_PROMPT_CHARS", 24000) or 24000)
    )
    limit = max(4000, min(limit, 48000))
    timeout = max(
        45.0,
        float(getattr(settings, "CEREBRAS_TIMEOUT_SEC", 45) or 45),
    )
    out_tokens = max(256, min(int(max_tokens or 800), 4000))
    last_exc: Exception | None = None
    for shrink in (limit, max(4000, limit // 2), 6000):
        user_prompt = _trim_prompt(prompt, limit=shrink)
        try:
            result = cerebras_chat_completion(
                messages=[
                    {"role": "system", "content": _briefing_system_msg()},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=out_tokens,
                temperature=0.15,
                model=model,
                timeout=timeout,
                rotate_on_rate_limit=True,
                try_fallback_models=True,
            )
            text = str(result.get("text") or "").strip()
            if not text:
                raise AIProviderError("Cerebras returned empty briefing text")
            return {
                "provider": "cerebras",
                "text": text,
                "raw": {
                    "id": result.get("raw_id"),
                    "model": result.get("model"),
                    "key_fp": result.get("key_fp"),
                    "prompt_chars": len(user_prompt),
                },
            }
        except RuntimeError as exc:
            last_exc = exc
            msg = str(exc).casefold()
            if "413" in msg or "too large" in msg or "payload" in msg:
                logger.warning(
                    "cerebras payload too large — shrink prompt (%s chars)",
                    shrink,
                )
                continue
            raise AIProviderError(str(exc)) from exc
    raise AIProviderError(str(last_exc or "Cerebras request failed"))


def openrouter_complete(
    prompt: str,
    *,
    max_tokens: int = 400,
    model: str | None = None,
    prompt_limit: int | None = None,
) -> dict[str, Any]:
    """OpenRouter free-model briefing fallback (after Groq / Cerebras)."""
    from apps.integrations.ai.openrouter_pool import (
        openrouter_chat_completion,
        openrouter_enabled,
    )

    if not openrouter_enabled():
        raise AIProviderError("OpenRouter is disabled or has no API keys")
    limit = int(
        prompt_limit
        if prompt_limit is not None
        else (
            getattr(settings, "AI_BRIEFING_OPENROUTER_PROMPT_CHARS", None)
            or getattr(settings, "AI_BRIEFING_GROQ_PROMPT_CHARS", 10000)
            or 10000
        )
    )
    limit = max(4000, min(limit, 32000))
    timeout = max(
        60.0,
        float(getattr(settings, "OPENROUTER_TIMEOUT_SEC", 45) or 45),
    )
    out_tokens = max(256, min(int(max_tokens or 800), 4000))
    last_exc: Exception | None = None
    for shrink in (limit, max(3000, limit // 2), 3500):
        user_prompt = _trim_prompt(prompt, limit=shrink)
        try:
            result = openrouter_chat_completion(
                messages=[
                    {"role": "system", "content": _briefing_system_msg()},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=out_tokens,
                temperature=0.15,
                model=model,
                timeout=timeout,
                rotate_on_rate_limit=True,
                try_fallback_models=True,
            )
            text = str(result.get("text") or "").strip()
            if not text:
                raise AIProviderError("OpenRouter returned empty briefing text")
            return {
                "provider": "openrouter",
                "text": text,
                "raw": {
                    "id": result.get("raw_id"),
                    "model": result.get("model"),
                    "key_fp": result.get("key_fp"),
                    "prompt_chars": len(user_prompt),
                },
            }
        except RuntimeError as exc:
            last_exc = exc
            msg = str(exc).casefold()
            if "413" in msg or "too large" in msg or "payload" in msg:
                logger.warning(
                    "openrouter payload too large — shrink prompt (%s chars)",
                    shrink,
                )
                continue
            raise AIProviderError(str(exc)) from exc
    raise AIProviderError(str(last_exc or "OpenRouter request failed"))


def _trim_prompt(prompt: str, *, limit: int = 18000) -> str:
    text = (prompt or "").strip()
    if len(text) <= limit:
        return text
    # Prefer keeping the Wigolo draft (usually near the end) + instructions head.
    head = text[: int(limit * 0.55)]
    tail = text[-int(limit * 0.40) :]
    return head.rstrip() + "\n\n[…truncated middle evidence…]\n\n" + tail.lstrip()


def _ollama_complete(prompt: str, *, max_tokens: int = 1200) -> dict[str, Any]:
    if not getattr(settings, "OLLAMA_ENABLED", False):
        raise AIProviderError("Ollama disabled")
    base = (getattr(settings, "OLLAMA_BASE_URL", "") or "").strip().rstrip("/")
    if not base:
        raise AIProviderError("OLLAMA_BASE_URL missing")
    model = (
        getattr(settings, "OLLAMA_BRIEFING_MODEL", None)
        or getattr(settings, "OLLAMA_TRANSLATE_MODEL", "qwen2.5:3b")
        or "qwen2.5:3b"
    )
    timeout = float(getattr(settings, "OLLAMA_BRIEFING_TIMEOUT_SEC", 180) or 180)
    num_predict = max(256, min(int(max_tokens or 800), 4000))
    num_ctx = int(getattr(settings, "OLLAMA_NUM_CTX", 4096) or 4096)
    body = {
        "model": model,
        "stream": False,
        "keep_alive": getattr(settings, "OLLAMA_KEEP_ALIVE", "10m"),
        "options": {
            "temperature": 0.1,
            "num_predict": num_predict,
            "num_ctx": max(2048, num_ctx),
        },
        "messages": [
            {
                "role": "system",
                "content": (
                    "Bạn là biên tập viên OSINT quốc phòng Trung Quốc. "
                    "Viết tiếng Việt rõ ràng, ngắn, bám sát nguồn. "
                    "CẤM markdown (** * # và tiêu đề **1. …:**). "
                    "Dùng 1) 2) 3) hoặc • với sự kiện cụ thể (ai/cái gì/khi nào/ở đâu). "
                    "CẤM suy đoán/sáo rỗng không có trong nguồn; không chắc thì bỏ."
                ),
            },
            {"role": "user", "content": _trim_prompt(prompt, limit=7000)},
        ],
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(f"{base}/api/chat", json=body)
    except httpx.HTTPError as exc:
        raise AIProviderError(f"Ollama request failed: {exc}") from exc
    data = response.json() if response.content else {}
    if response.status_code >= 400:
        raise AIProviderError(f"Ollama HTTP {response.status_code}: {data}")
    message = data.get("message") if isinstance(data, dict) else {}
    text = ""
    if isinstance(message, dict):
        text = str(message.get("content") or "").strip()
    if not text:
        text = str(data.get("response") or "").strip()
    if not text:
        raise AIProviderError("Ollama returned empty briefing text")
    return {
        "provider": "ollama",
        "text": text,
        "raw": {"model": model},
    }


def _anthropic_complete(prompt: str, api_key: str, *, max_tokens: int) -> dict[str, Any]:
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": getattr(settings, "ANTHROPIC_MODEL", "claude-3-haiku-20240307"),
        "max_tokens": max_tokens,
        "system": (
            "Bạn là biên tập viên OSINT quốc phòng. "
            "Viết tiếng Việt rõ ràng, ngắn, bám sát bằng chứng. "
            "CẤM markdown (** * # và tiêu đề **1. …:**). "
            "Dùng 1) 2) 3) hoặc • với sự kiện cụ thể từ nguồn. "
            "CẤM suy đoán/sáo rỗng; không chắc thì bỏ. Không bịa số liệu."
        ),
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise AIProviderError(f"Anthropic request failed: {exc}") from exc

    data = response.json() if response.content else {}
    if response.status_code >= 400:
        # Do not include API key; message may contain provider error only
        raise AIProviderError(
            f"Anthropic HTTP {response.status_code}: {data.get('error', data)}"
        )

    parts = data.get("content") or []
    text = ""
    for part in parts:
        if isinstance(part, dict) and part.get("type") == "text":
            text += part.get("text", "")
    return {"provider": "anthropic", "text": text.strip(), "raw": {"id": data.get("id")}}


def _huggingface_complete(prompt: str, token: str, *, model: str) -> dict[str, Any]:
    url = f"https://api-inference.huggingface.co/models/{model}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=90.0) as client:
            response = client.post(
                url,
                headers=headers,
                json={"inputs": prompt[:4000], "parameters": {"max_new_tokens": 512}},
            )
    except httpx.HTTPError as exc:
        raise AIProviderError(f"Hugging Face request failed: {exc}") from exc

    data = response.json() if response.content else {}
    if response.status_code >= 400:
        raise AIProviderError(f"Hugging Face HTTP {response.status_code}: {data}")

    text = ""
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            text = first.get("summary_text") or first.get("generated_text") or ""
    elif isinstance(data, dict):
        text = data.get("summary_text") or data.get("generated_text") or str(data)

    return {
        "provider": "huggingface",
        "text": str(text).strip(),
        "raw": {"model": model},
    }


_BULLET_RE = re.compile(r"^[\-\*]\s+(.+)$")


def _question_from_prompt(prompt: str) -> str:
    """Derive a research question from the briefing evidence prompt."""
    text = prompt or ""
    for line in text.splitlines():
        low = line.casefold()
        if "osint summary for:" in low or "summary for:" in low:
            part = line.split(":", 1)[-1].strip()
            if len(part) >= 3:
                return f"Recent military/defense developments: {part[:160]}"
    bullets: list[str] = []
    for line in text.splitlines():
        m = _BULLET_RE.match(line.strip())
        if not m:
            continue
        item = m.group(1).strip()
        if item.lower().startswith("none") or item.startswith("No threat"):
            continue
        # Drop severity / source wrappers when present: "[high] Title (source=…)"
        cleaned = re.sub(r"^\[[^\]]+\]\s*", "", item)
        cleaned = re.sub(r"\s*\(source=[^)]*\)\s*$", "", cleaned, flags=re.I)
        if len(cleaned) >= 8:
            bullets.append(cleaned[:140])
        if len(bullets) >= 3:
            break
    if bullets:
        return (
            "China military and defense OSINT briefing covering: "
            + "; ".join(bullets)
        )
    return "China PLA military and defense developments last 24 hours Indo-Pacific"


def _wigolo_briefing_fallback(prompt: str) -> dict[str, Any]:
    """
    Open-web search/research digest when Groq/Ollama are unavailable.

    Prefer fast search first (usually enough). Optional research only when
    search is thin — research can take 30s+ and must not block HTTP workers.
    """
    if not bool(getattr(settings, "WIGOLO_BRIEFING_ENABLED", True)):
        raise AIProviderError("Wigolo briefing disabled")
    from apps.integrations.web_reader.wigolo import (
        research_wigolo,
        search_wigolo,
        wigolo_configured,
    )

    if not wigolo_configured():
        raise AIProviderError("Wigolo not configured")

    question = _question_from_prompt(prompt)

    def _rows_from_hits(hits: list) -> list[str]:
        rows: list[str] = []
        for hit in hits or []:
            title = str(hit.get("title") or "").strip()
            url = str(hit.get("url") or "").strip()
            snip = str(hit.get("content") or hit.get("snippet") or "").strip()
            if not (title or snip):
                continue
            line = f"• {title or '(untitled)'}"
            if snip:
                line += f" — {snip[:280]}"
            if url:
                line += f"\n  {url}"
            rows.append(line)
            if len(rows) >= 8:
                break
        return rows

    # 1) Fast path — search (balanced keeps latency predictable).
    try:
        hits = search_wigolo(
            question,
            limit=8,
            category="news",
            time_range="week",
            search_depth="balanced",
        )
    except Exception as exc:  # noqa: BLE001
        hits = []
        search_err = str(exc)[:160]
    else:
        search_err = ""

    rows = _rows_from_hits(hits)
    if len(rows) >= 3:
        text = "\n".join(
            [
                "Bản tin OSINT (Wigolo search — fallback)",
                "",
                "LLM nội bộ tạm không khả dụng. Tổng hợp open-web qua Wigolo:",
                "",
                f"Câu hỏi: {question}",
                "",
                "Nguồn mở",
                *rows,
                "",
                "Độ tin cậy",
                "• Digest tìm kiếm, chưa qua LLM viết lại — đối chiếu URL gốc.",
            ]
        )
        return {
            "provider": "wigolo",
            "text": text,
            "raw": {
                "mode": "wigolo_search",
                "hit_count": len(rows),
                "question": question[:200],
            },
        }

    # 2) Slow path — research only if search was thin and allowed.
    allow_research = bool(
        getattr(settings, "WIGOLO_BRIEFING_FALLBACK_RESEARCH", True)
    )
    depth = str(
        getattr(settings, "WIGOLO_BRIEFING_FALLBACK_DEPTH", "quick") or "quick"
    )
    if depth not in {"quick", "standard"}:
        depth = "quick"
    research_err = search_err
    if allow_research:
        research = research_wigolo(question, depth=depth, max_sources=6)
        md = str(research.get("markdown") or "").strip()
        if research.get("ok") and md and len(md) >= 80:
            text = (
                "Bản tin OSINT (Wigolo research — fallback)\n\n"
                "LLM nội bộ (Groq/Ollama) tạm không khả dụng. Dùng báo cáo Wigolo "
                "(có thể là heuristic nếu quota LLM phía Wigolo cũng hết).\n\n"
                f"{md}"
            )
            return {
                "provider": "wigolo",
                "text": text,
                "raw": {
                    "mode": "wigolo_research",
                    "depth": depth,
                    "question": question[:200],
                },
            }
        research_err = research.get("error") or research_err or "no research content"
        # Merge any research-adjacent empty with earlier thin search rows.
        if rows:
            text = "\n".join(
                [
                    "Bản tin OSINT (Wigolo search — fallback)",
                    "",
                    f"Câu hỏi: {question}",
                    "",
                    "Nguồn mở",
                    *rows,
                ]
            )
            return {
                "provider": "wigolo",
                "text": text,
                "raw": {
                    "mode": "wigolo_search_thin",
                    "hit_count": len(rows),
                    "question": question[:200],
                    "research_error": str(research_err)[:160],
                },
            }

    if rows:
        text = "\n".join(
            [
                "Bản tin OSINT (Wigolo search — fallback)",
                "",
                f"Câu hỏi: {question}",
                "",
                "Nguồn mở",
                *rows,
            ]
        )
        return {
            "provider": "wigolo",
            "text": text,
            "raw": {
                "mode": "wigolo_search_thin",
                "hit_count": len(rows),
                "question": question[:200],
            },
        }

    raise AIProviderError(research_err or "Wigolo returned no content")


def _local_briefing(prompt: str) -> str:
    """Structured Vietnamese digest when all LLM providers fail."""
    bullets: list[str] = []
    for line in (prompt or "").splitlines():
        m = _BULLET_RE.match(line.strip())
        if not m:
            continue
        item = m.group(1).strip()
        if item.lower().startswith("none") or item.startswith("No threat"):
            continue
        # Prefer Wire-formatted lines already cleaned.
        if item.startswith("Tóm tắt Wire:") or item.startswith("Gốc EN:"):
            continue
        bullets.append(item)
        if len(bullets) >= 18:
            break

    lines = [
        "BẢN TIN OSINT (chế độ local — LLM tạm không khả dụng)",
        "",
        "Groq/Ollama không phản hồi được lúc này. Đây là tóm tắt có cấu trúc "
        "từ bằng chứng Dòng tin đã thu thập (không suy diễn thêm).",
        "",
        "TÓM TẮT ĐIỀU HÀNH",
        (
            f"Có {len(bullets)} mục bằng chứng nổi bật trong cửa sổ báo cáo. "
            "Ưu tiên kiểm chứng các mục mức độ cao trước khi hành động."
            if bullets
            else "Không có bằng chứng đủ trong cửa sổ — cần mở rộng truy vấn hoặc chờ ingest."
        ),
        "",
        "DIỄN BIẾN CHÍNH (từ Dòng tin / open-web đã gắn)",
    ]
    if bullets:
        for b in bullets[:15]:
            lines.append(f"• {b}")
    else:
        lines.append("• (trống)")

    lines.extend(
        [
            "",
            "ĐỘ TIN CẬY VÀ LƯU Ý",
            "• Đây là digest cấu trúc, chưa qua LLM viết lại.",
            "• Phân biệt tin đã xác minh với tin chưa kiểm chứng; đối chiếu nguồn gốc khi có.",
            "",
            "CHỈ BÁO TIẾP THEO",
            "• Tiếp tục theo dõi Dòng tin / CVE / leak mới trong 24–48 giờ tới.",
            "• Thử lại tóm tắt AI khi hạn mức Groq hồi phục hoặc khi Ollama sẵn sàng.",
        ]
    )
    return "\n".join(lines)
