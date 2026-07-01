"""LLM clients (Gemini + OpenAI) with retry and JSONL run logging.

The run logger writes to `app/run logs/<job_id>/parse.jsonl` and `summarize.jsonl`
matching the contract used by the LLM-Dev page.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.exceptions import GeminiExtractionError, OpenAIExtractionError
from app.services.extraction.cost import (
    CostTracker,
    extract_gemini_usage,
    extract_openai_usage,
)

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
RETRY_MAX_DELAY = 60.0
RUN_LOG_DIR = Path("app") / "run logs"


class RunLogger:
    """Append-only JSONL logger with the same on-disk format the dev UI expects."""

    def __init__(self, *, job_id: str | None) -> None:
        self.job_id = job_id

    def write(self, stage: str, payload: dict[str, Any]) -> None:
        if not self.job_id or stage not in {"parse", "summarize"}:
            return
        try:
            run_dir = RUN_LOG_DIR / self.job_id
            run_dir.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                **payload,
            }
            with (run_dir / f"{stage}.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception:
            logger.warning("Run log write failed for job %s stage %s", self.job_id, stage, exc_info=True)


def loggable(value: Any) -> Any:
    if isinstance(value, list):
        return [loggable(item) for item in value]
    if isinstance(value, dict):
        if "base64" in value and isinstance(value["base64"], str):
            raw = value["base64"]
            return {
                **{k: loggable(v) for k, v in value.items() if k != "base64"},
                "base64_omitted": True,
                "base64_chars": len(raw),
                "base64_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            }
        return {k: loggable(v) for k, v in value.items()}
    return value


def _retry_delay(attempt: int) -> float:
    return min(RETRY_MAX_DELAY, float(2**attempt))


def _parse_jsonish(content: str, task_label: str) -> Any:
    candidates = [content.strip()]
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", content, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1).strip())
    object_start = content.find("{")
    object_end = content.rfind("}")
    if object_start >= 0 and object_end > object_start:
        candidates.append(content[object_start : object_end + 1])
    array_start = content.find("[")
    array_end = content.rfind("]")
    if array_start >= 0 and array_end > array_start:
        candidates.append(content[array_start : array_end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise GeminiExtractionError(
        f"{task_label} returned invalid JSON: {(content[:300] or 'empty').strip()}"
    )


async def gemini_json(
    *,
    model: str,
    system_prompt: str,
    user_text: str,
    images: list[bytes] | None = None,
    schema: dict[str, Any] | None = None,
    task_label: str,
    run_logger: RunLogger | None = None,
    stage: str = "parse",
    cost_tracker: CostTracker | None = None,
) -> Any:
    """Call Gemini, expect JSON back. Returns parsed dict/list."""
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_google_genai import ChatGoogleGenerativeAI
    except Exception as exc:  # noqa: BLE001
        raise GeminiExtractionError(
            "langchain-google-genai is not installed. Run pip install -r requirements.txt."
        ) from exc

    if not settings.GEMINI_API_KEY:
        raise GeminiExtractionError("GEMINI_API_KEY is not configured.")

    chat = ChatGoogleGenerativeAI(
        model=model,
        api_key=settings.GEMINI_API_KEY,
        temperature=0,
        max_tokens=65536,
        timeout=300,
        max_retries=0,
    )
    json_model = chat.bind(response_mime_type="application/json")

    schema_hint = (
        f"\n\nReturn JSON matching this schema:\n```json\n{json.dumps(schema, indent=2)}\n```"
        if schema
        else ""
    )
    schema_prompt = f"Return only valid JSON.{schema_hint}"

    content: list[dict[str, Any]] = [{"type": "text", "text": schema_prompt}]
    content.append({"type": "text", "text": user_text})
    if images:
        for img_bytes in images:
            content.append(
                {
                    "type": "image",
                    "base64": base64.b64encode(img_bytes).decode(),
                    "mime_type": "image/png",
                }
            )

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=content)]
    if run_logger:
        run_logger.write(
            stage,
            {
                "task_label": task_label,
                "provider": "gemini",
                "model": model,
                "direction": "input",
                "system_prompt": system_prompt,
                "user_prompt": loggable(content),
                "schema": schema,
            },
        )

    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await json_model.ainvoke(messages)
            raw_text = _extract_text(response)
            if not raw_text:
                raise GeminiExtractionError(f"{task_label} returned empty response.")
            parsed = _parse_jsonish(raw_text, task_label)
            usage_event: dict[str, Any] | None = None
            if cost_tracker is not None:
                input_tokens, output_tokens, cached = extract_gemini_usage(response)
                usage_event = cost_tracker.record(
                    provider="gemini",
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=cached,
                    stage=stage,
                )
            if run_logger:
                run_logger.write(
                    stage,
                    {
                        "task_label": task_label,
                        "provider": "gemini",
                        "model": model,
                        "direction": "output",
                        "raw_output": raw_text,
                        "parsed_output": loggable(parsed),
                        "usage": (
                            {
                                "input_tokens": usage_event["input_tokens"],
                                "output_tokens": usage_event["output_tokens"],
                                "cached_input_tokens": usage_event["cached_input_tokens"],
                                "cost_usd": usage_event["cost_usd"],
                            }
                            if usage_event
                            else None
                        ),
                    },
                )
            return parsed
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= MAX_RETRIES:
                break
            delay = _retry_delay(attempt)
            logger.warning(
                "%s gemini attempt %s/%s failed (%s). Retry in %.1fs.",
                task_label,
                attempt,
                MAX_RETRIES,
                str(exc)[:200],
                delay,
            )
            await asyncio.sleep(delay)

    raise GeminiExtractionError(f"{task_label} failed after retries: {last_exc}")


def _extract_text(raw: Any) -> str | None:
    if hasattr(raw, "text") and isinstance(raw.text, str):
        return raw.text
    if hasattr(raw, "content"):
        c = raw.content
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            parts: list[str] = []
            for p in c:
                if isinstance(p, str):
                    parts.append(p)
                elif isinstance(p, dict) and p.get("type") == "text":
                    parts.append(str(p.get("text", "")))
            return "\n".join(parts)
    return None


async def openai_json(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    task_label: str,
    run_logger: RunLogger | None = None,
    stage: str = "summarize",
    cost_tracker: CostTracker | None = None,
) -> dict[str, Any]:
    """Call OpenAI's responses API for a JSON object."""
    try:
        from openai import AsyncOpenAI
    except Exception as exc:  # noqa: BLE001
        raise OpenAIExtractionError("openai package is not installed.") from exc

    if not settings.OPENAI_API_KEY:
        raise OpenAIExtractionError("OPENAI_API_KEY is not configured.")

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": task_label.lower().replace(" ", "_"),
            "schema": schema,
            "strict": True,
        },
    }

    if run_logger:
        run_logger.write(
            stage,
            {
                "task_label": task_label,
                "provider": "openai",
                "model": model,
                "direction": "input",
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "schema": schema,
            },
        )

    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            completion = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=response_format,
            )
            content = completion.choices[0].message.content or ""
            parsed = _parse_jsonish(content, task_label)
            if not isinstance(parsed, dict):
                raise OpenAIExtractionError(f"{task_label} did not return a JSON object.")
            usage_event: dict[str, Any] | None = None
            if cost_tracker is not None:
                input_tokens, output_tokens, cached = extract_openai_usage(completion)
                usage_event = cost_tracker.record(
                    provider="openai",
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=cached,
                    stage=stage,
                )
            if run_logger:
                run_logger.write(
                    stage,
                    {
                        "task_label": task_label,
                        "provider": "openai",
                        "model": model,
                        "direction": "output",
                        "raw_output": content,
                        "parsed_output": parsed,
                        "usage": (
                            {
                                "input_tokens": usage_event["input_tokens"],
                                "output_tokens": usage_event["output_tokens"],
                                "cached_input_tokens": usage_event["cached_input_tokens"],
                                "cost_usd": usage_event["cost_usd"],
                            }
                            if usage_event
                            else None
                        ),
                    },
                )
            return parsed
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= MAX_RETRIES:
                break
            delay = _retry_delay(attempt)
            logger.warning(
                "%s openai attempt %s/%s failed (%s). Retry in %.1fs.",
                task_label,
                attempt,
                MAX_RETRIES,
                str(exc)[:200],
                delay,
            )
            await asyncio.sleep(delay)

    raise OpenAIExtractionError(f"{task_label} failed after retries: {last_exc}")


async def openai_multimodal_json(
    *,
    model: str,
    system_prompt: str,
    user_text: str,
    images: list[bytes] | None = None,
    schema: dict[str, Any] | None = None,
    task_label: str,
    run_logger: RunLogger | None = None,
    stage: str = "parse",
    cost_tracker: CostTracker | None = None,
) -> Any:
    """Call OpenAI with optional images, expect JSON back. Returns parsed dict/list."""
    try:
        from openai import AsyncOpenAI
    except Exception as exc:  # noqa: BLE001
        raise OpenAIExtractionError("openai package is not installed.") from exc

    if not settings.OPENAI_API_KEY:
        raise OpenAIExtractionError("OPENAI_API_KEY is not configured.")

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    # Constrained decoding (strict json_schema) instead of the loose json_object
    # mode: the latter only guarantees syntactically-valid JSON, not schema
    # conformance, so nothing stops the model from degenerating into a
    # malformed/repetitive blob under load - confirmed in production logs as a
    # multi-page parse batch that returned thousands of repeated whitespace
    # characters instead of page data. Strict mode makes that failure mode
    # essentially impossible since the token grammar is constrained to the
    # schema at generation time.
    response_format: dict[str, Any] = {"type": "json_object"}
    if schema:
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": task_label.lower().replace(" ", "_").replace("/", "_"),
                "schema": schema,
                "strict": True,
            },
        }
    schema_hint = (
        f"\n\nReturn JSON matching this schema:\n```json\n{json.dumps(schema, indent=2)}\n```"
        if schema
        else ""
    )
    schema_prefix = f"Return only valid JSON.{schema_hint}"

    user_content: list[dict[str, Any]] = [
        {"type": "text", "text": schema_prefix},
        {"type": "text", "text": user_text},
    ]
    if images:
        for img_bytes in images:
            b64 = base64.b64encode(img_bytes).decode()
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{b64}",
                        "detail": settings.OPENAI_PAGE_IMAGE_DETAIL,
                    },
                }
            )

    loggable_content = [
        item if item.get("type") != "image_url" else {**item, "image_url": {"url": "[base64 omitted]"}}
        for item in user_content
    ]

    if run_logger:
        run_logger.write(
            stage,
            {
                "task_label": task_label,
                "provider": "openai",
                "model": model,
                "direction": "input",
                "system_prompt": system_prompt,
                "user_prompt": loggable_content,
                "schema": schema,
            },
        )

    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            completion = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                response_format=response_format,
            )
            raw_text = completion.choices[0].message.content or ""
            if not raw_text:
                raise OpenAIExtractionError(f"{task_label} returned empty response.")
            parsed = _parse_jsonish(raw_text, task_label)
            usage_event: dict[str, Any] | None = None
            if cost_tracker is not None:
                input_tokens, output_tokens, cached = extract_openai_usage(completion)
                usage_event = cost_tracker.record(
                    provider="openai",
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=cached,
                    stage=stage,
                )
            if run_logger:
                run_logger.write(
                    stage,
                    {
                        "task_label": task_label,
                        "provider": "openai",
                        "model": model,
                        "direction": "output",
                        "raw_output": raw_text,
                        "parsed_output": loggable(parsed),
                        "usage": (
                            {
                                "input_tokens": usage_event["input_tokens"],
                                "output_tokens": usage_event["output_tokens"],
                                "cached_input_tokens": usage_event["cached_input_tokens"],
                                "cost_usd": usage_event["cost_usd"],
                            }
                            if usage_event
                            else None
                        ),
                    },
                )
            return parsed
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= MAX_RETRIES:
                break
            delay = _retry_delay(attempt)
            logger.warning(
                "%s openai attempt %s/%s failed (%s). Retry in %.1fs.",
                task_label,
                attempt,
                MAX_RETRIES,
                str(exc)[:200],
                delay,
            )
            await asyncio.sleep(delay)

    raise OpenAIExtractionError(f"{task_label} failed after retries: {last_exc}")


def page_model() -> str:
    return settings.GEMINI_PAGE_MODEL if settings.AI_PROVIDER == "gemini" else settings.OPENAI_PAGE_MODEL


def opinion_model() -> str:
    return settings.OPENAI_BUNDLE_MODEL
