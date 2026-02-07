"""
OpenRouter unified API client for multi-LLM access.

Handles async requests, retries, rate limiting, and cost tracking
through a single OpenRouter endpoint.
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel
from rich.console import Console

load_dotenv()

console = Console()

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 2.0  # seconds


class UsageRecord(BaseModel):
    """Record of a single API call's token usage and cost."""

    model_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    timestamp: float = 0.0


@dataclass
class CostTracker:
    """Tracks cumulative API costs across all calls."""

    records: list[UsageRecord] = field(default_factory=list)
    budget_usd: float | None = None

    @property
    def total_cost(self) -> float:
        return sum(r.cost_usd for r in self.records)

    @property
    def total_tokens(self) -> int:
        return sum(r.total_tokens for r in self.records)

    def add(self, record: UsageRecord) -> None:
        self.records.append(record)

    def is_over_budget(self) -> bool:
        if self.budget_usd is None:
            return False
        return self.total_cost >= self.budget_usd

    def summary_by_model(self) -> dict[str, dict[str, Any]]:
        """Get cost/token summary grouped by model."""
        summary: dict[str, dict[str, Any]] = {}
        for r in self.records:
            if r.model_id not in summary:
                summary[r.model_id] = {
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                    "avg_latency_ms": 0.0,
                }
            s = summary[r.model_id]
            s["calls"] += 1
            s["prompt_tokens"] += r.prompt_tokens
            s["completion_tokens"] += r.completion_tokens
            s["total_tokens"] += r.total_tokens
            s["cost_usd"] += r.cost_usd
            s["avg_latency_ms"] = (
                (s["avg_latency_ms"] * (s["calls"] - 1) + r.latency_ms)
                / s["calls"]
            )
        return summary


class OpenRouterClient:
    """Async client for OpenRouter API with retry and cost tracking."""

    def __init__(
        self,
        api_key: str | None = None,
        budget_usd: float | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required. "
                "Set it in .env or pass it directly."
            )
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.cost_tracker = CostTracker(budget_usd=budget_usd)
        self._semaphore = asyncio.Semaphore(4)  # Max concurrent requests

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/LLM-cross-eval",
            "X-Title": "LLM Cross-Evaluation System",
        }

    async def chat_completion(
        self,
        model_id: str,
        messages: list[dict[str, str]],
        max_tokens: int = 4096,
        temperature: float = 0.7,
        json_mode: bool = False,
    ) -> dict[str, Any]:
        """
        Send a chat completion request to OpenRouter.

        Args:
            model_id: OpenRouter model identifier (e.g., 'anthropic/claude-sonnet-4')
            messages: List of message dicts with 'role' and 'content'
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature
            json_mode: If True, request JSON response format

        Returns:
            Dict with 'content', 'usage', and 'latency_ms' keys
        """
        if self.cost_tracker.is_over_budget():
            raise RuntimeError(
                f"Budget exceeded: ${self.cost_tracker.total_cost:.4f} "
                f">= ${self.cost_tracker.budget_usd:.4f}"
            )

        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        async with self._semaphore:
            return await self._request_with_retry(model_id, payload)

    async def _request_with_retry(
        self, model_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute request with exponential backoff retry."""
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                start_time = time.monotonic()
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(
                        OPENROUTER_BASE_URL,
                        headers=self._headers(),
                        json=payload,
                    )
                elapsed_ms = (time.monotonic() - start_time) * 1000

                if response.status_code == 429:
                    # Rate limited — wait and retry
                    wait = self.retry_delay * (2**attempt)
                    console.print(
                        f"[yellow]Rate limited on {model_id}, "
                        f"retrying in {wait:.1f}s...[/yellow]"
                    )
                    await asyncio.sleep(wait)
                    continue

                response.raise_for_status()
                data = response.json()

                # Extract content
                content = ""
                if data.get("choices"):
                    content = data["choices"][0].get("message", {}).get(
                        "content", ""
                    )

                # Extract usage info
                usage = data.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                total_tokens = prompt_tokens + completion_tokens

                # Estimate cost from OpenRouter's response or usage
                cost = 0.0
                if "total_cost" in usage:
                    cost = usage["total_cost"]

                record = UsageRecord(
                    model_id=model_id,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    cost_usd=cost,
                    latency_ms=elapsed_ms,
                    timestamp=time.time(),
                )
                self.cost_tracker.add(record)

                return {
                    "content": content,
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                    },
                    "latency_ms": elapsed_ms,
                    "model_id": model_id,
                }

            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code >= 500:
                    wait = self.retry_delay * (2**attempt)
                    console.print(
                        f"[yellow]Server error {e.response.status_code} "
                        f"on {model_id}, retrying in {wait:.1f}s...[/yellow]"
                    )
                    await asyncio.sleep(wait)
                    continue
                raise
            except (httpx.ConnectError, httpx.ReadTimeout) as e:
                last_error = e
                wait = self.retry_delay * (2**attempt)
                console.print(
                    f"[yellow]Connection error on {model_id}, "
                    f"retrying in {wait:.1f}s...[/yellow]"
                )
                await asyncio.sleep(wait)
                continue

        raise RuntimeError(
            f"Failed after {self.max_retries} retries for {model_id}: "
            f"{last_error}"
        )

    async def batch_chat_completion(
        self,
        requests: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Execute multiple chat completions in parallel.

        Args:
            requests: List of dicts, each with keys matching
                      chat_completion parameters (model_id, messages, etc.)

        Returns:
            List of results in the same order as requests
        """
        tasks = [self.chat_completion(**req) for req in requests]
        return await asyncio.gather(*tasks, return_exceptions=True)


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences (```json ... ```) from text."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        start = 1
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip() == "```":
                end = i
                break
        text = "\n".join(lines[start:end]).strip()
    return text


def _repair_json_text(text: str) -> str:
    """
    Attempt to repair common JSON malformations from LLM output.

    Handles:
    - Trailing commas before ] or }
    - Unterminated strings (close them)
    - Truncated JSON (close open braces/brackets)
    - Single quotes instead of double quotes
    - Unquoted property names
    """
    import re

    # Remove trailing commas before } or ]
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # Fix single-quoted strings → double-quoted (simple heuristic)
    # Only if there are no double quotes around values
    if text.count("'") > text.count('"'):
        text = re.sub(r"(?<!\\)'", '"', text)

    # Try to close unterminated strings:
    # Count unmatched quotes — if odd, append a closing quote
    in_string = False
    escaped = False
    last_string_start = -1
    for i, ch in enumerate(text):
        if escaped:
            escaped = False
            continue
        if ch == '\\':
            escaped = True
            continue
        if ch == '"':
            if in_string:
                in_string = False
            else:
                in_string = True
                last_string_start = i

    if in_string:
        # We're inside an unterminated string — close it
        text += '"'

    # Close any unclosed brackets/braces
    open_braces = 0
    open_brackets = 0
    in_str = False
    esc = False
    for ch in text:
        if esc:
            esc = False
            continue
        if ch == '\\' and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == '{':
            open_braces += 1
        elif ch == '}':
            open_braces -= 1
        elif ch == '[':
            open_brackets += 1
        elif ch == ']':
            open_brackets -= 1

    # Remove trailing commas again (may have appeared after string fix)
    text = re.sub(r",\s*$", "", text)

    # Append missing closing delimiters
    text += ']' * max(0, open_brackets)
    text += '}' * max(0, open_braces)

    return text


def _extract_json_object(text: str) -> str:
    """Extract the first JSON object {...} from text, ignoring surrounding prose."""
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if esc:
            esc = False
            continue
        if ch == '\\' and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start:i + 1]
    # If we found a start but JSON is truncated, return from start
    if start >= 0:
        return text[start:]
    return text


def parse_json_response(content: str) -> dict[str, Any]:
    """
    Parse JSON from LLM response with multi-strategy repair.

    Strategies (in order):
    1. Direct parse after stripping code fences
    2. Extract JSON object from surrounding prose, then parse
    3. Repair common malformations (trailing commas, unterminated strings,
       unclosed braces), then parse
    4. Use json_repair library as last resort (if available)

    Args:
        content: Raw LLM response text

    Returns:
        Parsed JSON dict

    Raises:
        json.JSONDecodeError: If all repair strategies fail
    """
    text = _strip_code_fences(content)

    # Strategy 1: Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Extract JSON object from surrounding text
    extracted = _extract_json_object(text)
    if extracted != text:
        try:
            return json.loads(extracted)
        except json.JSONDecodeError:
            pass

    # Strategy 3: Repair common issues
    repaired = _repair_json_text(extracted if extracted != text else text)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Strategy 4: json_repair library (if installed)
    try:
        from json_repair import repair_json
        result = repair_json(text, return_objects=True)
        if isinstance(result, dict):
            return result
    except ImportError:
        pass

    # Strategy 5: Aggressive repair — try on the original extracted text
    # with additional fixes
    aggressive = _repair_json_text(text)
    try:
        return json.loads(aggressive)
    except json.JSONDecodeError as e:
        # Final attempt: try json_repair on repaired text
        try:
            from json_repair import repair_json
            result = repair_json(repaired, return_objects=True)
            if isinstance(result, dict):
                return result
        except (ImportError, Exception):
            pass
        raise e
