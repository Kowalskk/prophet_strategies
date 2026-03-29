"""
LLM Pre-Trade Filter — evaluates market context before opening positions.

Uses Claude (or any OpenAI-compatible API) to analyze whether a trade signal
makes sense given current market conditions and news context.

Configuration (env vars / .env)
-------------------------------
- LLM_FILTER_ENABLED: true/false (default: false)
- ANTHROPIC_API_KEY: API key for Claude
- LLM_FILTER_MODEL: model to use (default: claude-haiku-4-5-20251001)
- LLM_FILTER_MIN_CONFIDENCE: minimum LLM confidence to approve (default: 0.6)

The filter is called after the risk manager approves a signal but before
it gets persisted. It adds a small latency (~1-2s) per signal but prevents
low-quality trades from executing.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_ENABLED = os.getenv("LLM_FILTER_ENABLED", "false").lower() == "true"
_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
_MODEL = os.getenv("LLM_FILTER_MODEL", "claude-haiku-4-5-20251001")
_MIN_CONFIDENCE = float(os.getenv("LLM_FILTER_MIN_CONFIDENCE", "0.6"))
_API_URL = "https://api.anthropic.com/v1/messages"
_TIMEOUT = 15.0


SYSTEM_PROMPT = """You are a trading risk analyst for a Polymarket prediction market bot.
You receive trade signals and must evaluate whether they make sense.

You will be given:
- The market question (what is being predicted)
- The strategy that generated the signal
- The proposed trade (side, price, size)
- Current orderbook context

You must respond with ONLY a JSON object:
{
  "approve": true/false,
  "confidence": 0.0-1.0,
  "reasoning": "one sentence explanation"
}

Approve criteria:
- The trade aligns with reasonable market expectations
- The price offers genuine value (not buying at fair value)
- The market hasn't likely already resolved (e.g., event already happened)
- The strategy logic makes sense for this specific market

Reject criteria:
- Market question suggests the event already happened
- Price is too close to 1.0 or 0.0 with no edge
- The market is about something completely unpredictable with no edge
- Position size is disproportionate to the edge

Be FAST. Keep reasoning under 20 words. Default to approve if unsure."""


class LLMFilter:
    """Pre-trade filter using LLM analysis."""

    def __init__(self) -> None:
        self._enabled = _ENABLED and bool(_API_KEY)
        self._client: httpx.AsyncClient | None = None
        self._stats = {"calls": 0, "approved": 0, "rejected": 0, "errors": 0}

        if _ENABLED and not _API_KEY:
            logger.warning(
                "LLM_FILTER_ENABLED=true but ANTHROPIC_API_KEY not set — filter disabled"
            )
        if self._enabled:
            logger.info("LLMFilter enabled with model=%s min_conf=%.2f", _MODEL, _MIN_CONFIDENCE)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    async def start(self) -> None:
        if self._enabled and self._client is None:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def evaluate(
        self,
        market_question: str,
        strategy_name: str,
        side: str,
        target_price: float,
        size_usd: float,
        orderbook_context: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Evaluate a trade signal with the LLM.

        Returns
        -------
        (approved, reason)
            If not enabled, always returns (True, "LLM filter disabled").
        """
        if not self._enabled:
            return True, "LLM filter disabled"

        if self._client is None:
            await self.start()

        self._stats["calls"] += 1

        ob_context = ""
        if orderbook_context:
            ob_context = (
                f"\nOrderbook: best_bid={orderbook_context.get('best_bid', '?')} "
                f"best_ask={orderbook_context.get('best_ask', '?')} "
                f"spread={orderbook_context.get('spread', '?')}"
            )

        user_msg = (
            f"Market: {market_question}\n"
            f"Strategy: {strategy_name}\n"
            f"Signal: BUY {side} @ ${target_price:.4f}, size=${size_usd:.2f}"
            f"{ob_context}"
        )

        try:
            resp = await self._client.post(
                _API_URL,
                headers={
                    "x-api-key": _API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": _MODEL,
                    "max_tokens": 150,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_msg}],
                },
            )

            if resp.status_code != 200:
                logger.warning("LLM filter API error: %d", resp.status_code)
                self._stats["errors"] += 1
                return True, f"LLM API error {resp.status_code} — defaulting to approve"

            data = resp.json()
            content = data.get("content", [{}])[0].get("text", "{}")

            # Parse JSON response
            result = json.loads(content)
            approved = result.get("approve", True)
            confidence = float(result.get("confidence", 1.0))
            reasoning = result.get("reasoning", "no reason given")

            # Apply minimum confidence threshold
            if approved and confidence < _MIN_CONFIDENCE:
                approved = False
                reasoning = f"Low confidence ({confidence:.2f} < {_MIN_CONFIDENCE}): {reasoning}"

            if approved:
                self._stats["approved"] += 1
                logger.debug(
                    "LLM APPROVED: %s %s@%.4f (conf=%.2f) — %s",
                    strategy_name, side, target_price, confidence, reasoning,
                )
            else:
                self._stats["rejected"] += 1
                logger.info(
                    "LLM REJECTED: %s %s@%.4f (conf=%.2f) — %s",
                    strategy_name, side, target_price, confidence, reasoning,
                )

            return approved, reasoning

        except json.JSONDecodeError:
            logger.warning("LLM filter returned non-JSON response")
            self._stats["errors"] += 1
            return True, "LLM response parse error — defaulting to approve"
        except Exception as exc:
            logger.warning("LLM filter error: %s", exc)
            self._stats["errors"] += 1
            return True, f"LLM error: {exc} — defaulting to approve"


# Module-level singleton
llm_filter = LLMFilter()
