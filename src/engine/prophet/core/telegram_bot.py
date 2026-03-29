"""
Telegram Bot — sends real-time trade alerts and daily summaries.

Features
--------
- Trade alerts: new position opened, position closed (win/loss)
- Error alerts: scheduler errors, risk manager rejections
- Daily summary: PnL, win rate, open positions, top strategies
- Manual commands: /status, /pnl, /positions

Configuration (env vars / .env)
-------------------------------
- TELEGRAM_BOT_TOKEN: Bot API token from @BotFather
- TELEGRAM_CHAT_ID: Your chat/group ID (use @userinfobot to find it)
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
_BASE_URL = "https://api.telegram.org/bot{token}"
_TIMEOUT = 10.0


class TelegramNotifier:
    """Async Telegram notification sender."""

    def __init__(
        self,
        bot_token: str = "",
        chat_id: str = "",
    ) -> None:
        self.bot_token = bot_token or _BOT_TOKEN
        self.chat_id = chat_id or _CHAT_ID
        self._client: httpx.AsyncClient | None = None
        self._enabled = bool(self.bot_token and self.chat_id)

        if not self._enabled:
            logger.warning(
                "TelegramNotifier: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — "
                "notifications disabled"
            )

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def start(self) -> None:
        """Start the HTTP client."""
        if self._enabled and self._client is None:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT)
            logger.info("TelegramNotifier started.")

    async def stop(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Low-level send
    # ------------------------------------------------------------------

    async def _send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message to the configured chat. Returns True on success."""
        if not self._enabled:
            return False
        if self._client is None:
            await self.start()

        url = f"{_BASE_URL.format(token=self.bot_token)}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        try:
            resp = await self._client.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning("Telegram send failed: %d %s", resp.status_code, resp.text[:200])
                return False
            return True
        except Exception as exc:
            logger.warning("Telegram send error: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Trade alerts
    # ------------------------------------------------------------------

    async def notify_trade_opened(
        self,
        strategy: str,
        market_question: str,
        side: str,
        entry_price: float,
        size_usd: float,
    ) -> None:
        """Alert when a new position is opened."""
        text = (
            f"🟢 <b>TRADE OPENED</b>\n"
            f"Strategy: <code>{strategy}</code>\n"
            f"Market: {_escape(market_question[:80])}\n"
            f"Side: {side} @ ${entry_price:.4f}\n"
            f"Size: ${size_usd:.2f}"
        )
        await self._send(text)

    async def notify_trade_closed(
        self,
        strategy: str,
        market_question: str,
        side: str,
        entry_price: float,
        exit_price: float,
        net_pnl: float,
        exit_reason: str,
    ) -> None:
        """Alert when a position is closed."""
        emoji = "✅" if net_pnl >= 0 else "🔴"
        pnl_sign = "+" if net_pnl >= 0 else ""
        text = (
            f"{emoji} <b>TRADE CLOSED</b>\n"
            f"Strategy: <code>{strategy}</code>\n"
            f"Market: {_escape(market_question[:80])}\n"
            f"Side: {side} | Entry: ${entry_price:.4f} → Exit: ${exit_price:.4f}\n"
            f"PnL: <b>{pnl_sign}${net_pnl:.2f}</b>\n"
            f"Reason: {exit_reason}"
        )
        await self._send(text)

    async def notify_error(self, component: str, error_msg: str) -> None:
        """Alert on critical errors."""
        text = (
            f"⚠️ <b>ERROR</b>\n"
            f"Component: <code>{component}</code>\n"
            f"Error: {_escape(error_msg[:300])}"
        )
        await self._send(text)

    async def notify_risk_rejection(
        self, strategy: str, market_id: int, reason: str
    ) -> None:
        """Alert when risk manager rejects a signal (batched — only unusual ones)."""
        # Don't spam on position limit hits — only alert on drawdown/loss
        if "positions limit" in reason.lower():
            return
        text = (
            f"🛡️ <b>RISK REJECTION</b>\n"
            f"Strategy: <code>{strategy}</code>\n"
            f"Market ID: {market_id}\n"
            f"Reason: {reason}"
        )
        await self._send(text)

    # ------------------------------------------------------------------
    # Daily summary
    # ------------------------------------------------------------------

    async def send_daily_summary(self, stats: dict[str, Any]) -> None:
        """Send a daily performance summary.

        Parameters
        ----------
        stats : dict with keys:
            - total_pnl (float)
            - today_pnl (float)
            - open_positions (int)
            - closed_today (int)
            - win_rate (float, 0-1)
            - top_strategies (list of (name, pnl) tuples)
            - total_exposure_usd (float)
        """
        pnl = stats.get("total_pnl", 0)
        today = stats.get("today_pnl", 0)
        sign_total = "+" if pnl >= 0 else ""
        sign_today = "+" if today >= 0 else ""
        wr = stats.get("win_rate", 0) * 100

        strat_lines = ""
        for name, spnl in stats.get("top_strategies", [])[:5]:
            s = "+" if spnl >= 0 else ""
            strat_lines += f"  • <code>{name}</code>: {s}${spnl:.2f}\n"

        text = (
            f"📊 <b>DAILY SUMMARY</b> — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Total PnL: <b>{sign_total}${pnl:.2f}</b>\n"
            f"Today: {sign_today}${today:.2f}\n"
            f"Win Rate: {wr:.1f}%\n"
            f"Open Positions: {stats.get('open_positions', 0)}\n"
            f"Closed Today: {stats.get('closed_today', 0)}\n"
            f"Exposure: ${stats.get('total_exposure_usd', 0):.2f}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<b>Top Strategies:</b>\n{strat_lines}"
        )
        await self._send(text)

    # ------------------------------------------------------------------
    # Build daily stats from DB
    # ------------------------------------------------------------------

    async def build_and_send_daily_summary(self) -> None:
        """Query DB and send the daily summary. Called by scheduler."""
        try:
            from prophet.db.database import get_session
            from prophet.db.models import Position
            from sqlalchemy import func, select

            async with get_session() as db:
                today_start = datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )

                # Total PnL
                total_stmt = select(func.sum(Position.net_pnl)).where(
                    Position.status == "closed"
                )
                total_pnl = (await db.execute(total_stmt)).scalar_one_or_none() or 0.0

                # Today PnL
                today_stmt = select(func.sum(Position.net_pnl)).where(
                    Position.status == "closed",
                    Position.closed_at >= today_start,
                )
                today_pnl = (await db.execute(today_stmt)).scalar_one_or_none() or 0.0

                # Open positions
                open_stmt = select(func.count()).select_from(Position).where(
                    Position.status == "open"
                )
                open_count = (await db.execute(open_stmt)).scalar_one() or 0

                # Closed today
                closed_today_stmt = select(func.count()).select_from(Position).where(
                    Position.status == "closed",
                    Position.closed_at >= today_start,
                )
                closed_today = (await db.execute(closed_today_stmt)).scalar_one() or 0

                # Win rate
                wins_stmt = select(func.count()).select_from(Position).where(
                    Position.status == "closed",
                    Position.net_pnl > 0,
                )
                wins = (await db.execute(wins_stmt)).scalar_one() or 0
                total_closed_stmt = select(func.count()).select_from(Position).where(
                    Position.status == "closed"
                )
                total_closed = (await db.execute(total_closed_stmt)).scalar_one() or 0
                win_rate = wins / total_closed if total_closed > 0 else 0.0

                # Exposure
                exposure_stmt = select(func.sum(Position.size_usd)).where(
                    Position.status == "open"
                )
                exposure = (await db.execute(exposure_stmt)).scalar_one_or_none() or 0.0

                # Top strategies by PnL
                top_stmt = (
                    select(Position.strategy, func.sum(Position.net_pnl).label("pnl"))
                    .where(Position.status == "closed")
                    .group_by(Position.strategy)
                    .order_by(func.sum(Position.net_pnl).desc())
                    .limit(5)
                )
                top_result = (await db.execute(top_stmt)).all()
                top_strategies = [(row[0], float(row[1])) for row in top_result]

            await self.send_daily_summary({
                "total_pnl": total_pnl,
                "today_pnl": today_pnl,
                "open_positions": open_count,
                "closed_today": closed_today,
                "win_rate": win_rate,
                "top_strategies": top_strategies,
                "total_exposure_usd": exposure,
            })
        except Exception as exc:
            logger.error("Failed to build/send daily summary: %s", exc)


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

notifier = TelegramNotifier()


def _escape(text: str) -> str:
    """Escape HTML special characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
