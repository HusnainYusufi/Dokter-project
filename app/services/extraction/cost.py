"""LLM usage / cost tracker.

Aggregates token counts and USD cost across every Gemini and OpenAI call made
during a single extraction job, so the UI can show live spend per job and per
pipeline step.

Pricing is a best-effort table of public per-model rates ($ / 1M tokens).
Unknown models default to 0 so we never invent fake costs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass(frozen=True)
class ModelPricing:
    """Per-million-token pricing in USD."""

    input_per_mtok: float
    output_per_mtok: float
    cached_input_per_mtok: float | None = None


_PRICING: dict[str, ModelPricing] = {
    "gemini-3-flash-preview": ModelPricing(0.50, 3.00),
    "gemini-2.5-flash-lite": ModelPricing(0.10, 0.40),
    "gemini-2.5-flash": ModelPricing(0.30, 2.50),
    "gemini-2.5-pro": ModelPricing(1.25, 10.0),
    "gemini-2.0-flash": ModelPricing(0.10, 0.40),
    "gemini-1.5-flash": ModelPricing(0.075, 0.30),
    "gemini-1.5-pro": ModelPricing(1.25, 5.00),
    "gpt-4.1": ModelPricing(2.00, 8.00, cached_input_per_mtok=0.50),
    "gpt-4.1-mini": ModelPricing(0.40, 1.60, cached_input_per_mtok=0.10),
    "gpt-4.1-nano": ModelPricing(0.10, 0.40, cached_input_per_mtok=0.025),
    "gpt-4o": ModelPricing(2.50, 10.0, cached_input_per_mtok=1.25),
    "gpt-4o-mini": ModelPricing(0.15, 0.60, cached_input_per_mtok=0.075),
    "gpt-5": ModelPricing(5.00, 15.00),
    "gpt-5.2": ModelPricing(8.00, 24.00),
    "o1": ModelPricing(15.00, 60.00),
    "o1-mini": ModelPricing(3.00, 12.00),
    "o3-mini": ModelPricing(1.10, 4.40),
}


def _lookup_pricing(model: str) -> ModelPricing:
    if not model:
        return ModelPricing(0.0, 0.0)
    if model in _PRICING:
        return _PRICING[model]
    lower = model.lower()
    if lower in _PRICING:
        return _PRICING[lower]
    for key, pricing in _PRICING.items():
        if lower.startswith(key.lower()):
            return pricing
    return ModelPricing(0.0, 0.0)


def estimate_cost(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> float:
    pricing = _lookup_pricing(model)
    input_cost = (input_tokens - cached_input_tokens) * pricing.input_per_mtok / 1_000_000
    output_cost = output_tokens * pricing.output_per_mtok / 1_000_000
    cached_cost = 0.0
    if cached_input_tokens and pricing.cached_input_per_mtok is not None:
        cached_cost = cached_input_tokens * pricing.cached_input_per_mtok / 1_000_000
    return max(0.0, input_cost + output_cost + cached_cost)


@dataclass
class StageTotals:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class ModelTotals(StageTotals):
    model: str = ""
    provider: str = ""


@dataclass
class CostTotals:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: float = 0.0
    by_stage: dict[str, StageTotals] = field(default_factory=dict)
    by_model: dict[str, ModelTotals] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "by_stage": {
                stage: {
                    "calls": v.calls,
                    "input_tokens": v.input_tokens,
                    "output_tokens": v.output_tokens,
                    "cached_input_tokens": v.cached_input_tokens,
                    "cost_usd": round(v.cost_usd, 6),
                }
                for stage, v in self.by_stage.items()
            },
            "by_model": [
                {
                    "model": v.model,
                    "provider": v.provider,
                    "calls": v.calls,
                    "input_tokens": v.input_tokens,
                    "output_tokens": v.output_tokens,
                    "cached_input_tokens": v.cached_input_tokens,
                    "cost_usd": round(v.cost_usd, 6),
                }
                for v in self.by_model.values()
            ],
        }


class CostTracker:
    """Thread-safe accumulator of per-model usage + cost.

    Optional ``on_update`` hook fires after every record (best-effort) so the
    pipeline can persist live cost while a job is running.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self.totals = CostTotals()
        self.on_update: callable | None = None

    def record(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        stage: str,
    ) -> dict[str, Any]:
        cost = estimate_cost(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
        )
        with self._lock:
            self.totals.calls += 1
            self.totals.input_tokens += input_tokens
            self.totals.output_tokens += output_tokens
            self.totals.cached_input_tokens += cached_input_tokens
            self.totals.cost_usd += cost

            stage_totals = self.totals.by_stage.setdefault(stage, StageTotals())
            stage_totals.calls += 1
            stage_totals.input_tokens += input_tokens
            stage_totals.output_tokens += output_tokens
            stage_totals.cached_input_tokens += cached_input_tokens
            stage_totals.cost_usd += cost

            model_key = f"{provider}:{model}"
            model_totals = self.totals.by_model.setdefault(
                model_key, ModelTotals(model=model, provider=provider)
            )
            model_totals.calls += 1
            model_totals.input_tokens += input_tokens
            model_totals.output_tokens += output_tokens
            model_totals.cached_input_tokens += cached_input_tokens
            model_totals.cost_usd += cost

            event = {
                "provider": provider,
                "model": model,
                "stage": stage,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_input_tokens": cached_input_tokens,
                "cost_usd": cost,
                "totals": self.totals.to_dict(),
            }
        if self.on_update is not None:
            try:
                self.on_update(event)
            except Exception:
                pass
        return event

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self.totals.to_dict()


def extract_gemini_usage(response: Any) -> tuple[int, int, int]:
    """Pull (input_tokens, output_tokens, cached_input_tokens) from a Gemini reply.

    Tolerates Langchain's `usage_metadata` and the raw google client shape.
    Returns zeros if nothing is reported.
    """
    metadata = getattr(response, "usage_metadata", None)
    if isinstance(metadata, dict):
        input_tokens = int(metadata.get("input_tokens") or metadata.get("prompt_token_count") or 0)
        output_tokens = int(metadata.get("output_tokens") or metadata.get("candidates_token_count") or 0)
        cached = int(
            metadata.get("input_token_details", {}).get("cache_read", 0)
            if isinstance(metadata.get("input_token_details"), dict)
            else metadata.get("cached_content_token_count") or 0
        )
        return input_tokens, output_tokens, cached
    if metadata is not None:
        input_tokens = int(getattr(metadata, "input_tokens", 0) or getattr(metadata, "prompt_token_count", 0) or 0)
        output_tokens = int(getattr(metadata, "output_tokens", 0) or getattr(metadata, "candidates_token_count", 0) or 0)
        cached = int(getattr(metadata, "cached_content_token_count", 0) or 0)
        return input_tokens, output_tokens, cached
    response_metadata = getattr(response, "response_metadata", None)
    if isinstance(response_metadata, dict):
        usage = response_metadata.get("usage_metadata") or response_metadata.get("usage")
        if isinstance(usage, dict):
            input_tokens = int(usage.get("input_tokens") or usage.get("prompt_token_count") or 0)
            output_tokens = int(usage.get("output_tokens") or usage.get("candidates_token_count") or 0)
            cached = int(usage.get("cached_content_token_count") or 0)
            return input_tokens, output_tokens, cached
    return 0, 0, 0


def extract_openai_usage(completion: Any) -> tuple[int, int, int]:
    """Pull (input_tokens, output_tokens, cached_input_tokens) from an OpenAI reply."""
    usage = getattr(completion, "usage", None)
    if usage is None:
        return 0, 0, 0
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    cached = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = int(getattr(details, "cached_tokens", 0) or 0)
    elif isinstance(usage, dict):
        details = usage.get("prompt_tokens_details") or {}
        cached = int(details.get("cached_tokens", 0) or 0)
    return input_tokens, output_tokens, cached
