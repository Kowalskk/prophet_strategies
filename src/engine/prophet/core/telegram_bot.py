"""
Telegram Bot — on-demand commands + smart alerts (no spam).

Notifications (automatic, filtered)
------------------------------------
- Daily summary at 20:00 UTC
- Trade closed only if |PnL| > threshold (skip tiny wins/losses)
- Critical errors only

Commands (on-demand, via polling)
---------------------------------
- /status  — engine health, uptime, last scan time
- /pnl     — total PnL, today, this week, win rate
- /strategies — top 10 strategies ranked by PnL
- /positions  — open positions, exposure, biggest positions
- /markets    — markets scanned per category
- /signals    — signals generated today + approval rate
- /help       — list all commands

Configuration (env vars)
------------------------
- TELEGRAM_BOT_TOKEN: Bot API token from @BotFather
- TELEGRAM_CHAT_ID: Your chat ID
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
_BASE_URL = "https://api.telegram.org/bot{token}"
_TIMEOUT = 10.0

# Only notify trades with PnL above these thresholds
_NOTIFY_WIN_THRESHOLD = 5.0    # notify wins > $5
_NOTIFY_LOSS_THRESHOLD = -10.0  # notify losses < -$10


class TelegramNotifier:
    """Async Telegram bot with command polling + smart alerts."""

    def __init__(self, bot_token: str = "", chat_id: str = "") -> None:
        self.bot_token = bot_token or _BOT_TOKEN
        self.chat_id = chat_id or _CHAT_ID
        self._client: httpx.AsyncClient | None = None
        self._enabled = bool(self.bot_token and self.chat_id)
        self._polling_task: asyncio.Task | None = None
        self._last_update_id: int = 0
        self._start_time: datetime | None = None

        if not self._enabled:
            logger.warning("TelegramNotifier: credentials not set — disabled")

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def start(self) -> None:
        if self._enabled and self._client is None:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT)
            self._start_time = datetime.now(timezone.utc)
            self._polling_task = asyncio.create_task(self._poll_commands())
            logger.info("TelegramNotifier started (polling commands).")

    async def stop(self) -> None:
        if self._polling_task is not None:
            self._polling_task.cancel()
            self._polling_task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Low-level send
    # ------------------------------------------------------------------

    async def _send(self, text: str, parse_mode: str = "HTML") -> bool:
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
            resp = await self._client.post(url, json=payload)  # type: ignore[union-attr]
            if resp.status_code != 200:
                logger.warning("Telegram send failed: %d %s", resp.status_code, resp.text[:200])
                return False
            return True
        except Exception as exc:
            logger.warning("Telegram send error: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Command polling
    # ------------------------------------------------------------------

    async def _poll_commands(self) -> None:
        """Long-poll Telegram for incoming commands."""
        while True:
            try:
                url = f"{_BASE_URL.format(token=self.bot_token)}/getUpdates"
                params = {"offset": self._last_update_id + 1, "timeout": 30}
                resp = await self._client.get(url, params=params, timeout=35.0)  # type: ignore[union-attr]
                if resp.status_code != 200:
                    await asyncio.sleep(5)
                    continue

                data = resp.json()
                for update in data.get("result", []):
                    self._last_update_id = update["update_id"]
                    msg = update.get("message", {})
                    text = msg.get("text", "")
                    chat_id = str(msg.get("chat", {}).get("id", ""))

                    # Only respond to our chat
                    if chat_id != self.chat_id:
                        continue

                    if text.startswith("/"):
                        cmd = text.split()[0].lower().split("@")[0]  # strip @botname
                        await self._handle_command(cmd)

            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.debug("Telegram poll error: %s", exc)
                await asyncio.sleep(10)

    async def _handle_command(self, cmd: str) -> None:
        handlers = {
            "/start": self._cmd_help,
            "/help": self._cmd_help,
            "/status": self._cmd_status,
            "/pnl": self._cmd_pnl,
            "/strategies": self._cmd_strategies,
            "/positions": self._cmd_positions,
            "/markets": self._cmd_markets,
            "/signals": self._cmd_signals,
        }
        handler = handlers.get(cmd)
        if handler:
            try:
                await handler()
            except Exception as exc:
                await self._send(f"Error ejecutando {cmd}: {_escape(str(exc)[:200])}")
        else:
            await self._send(f"Comando desconocido: {cmd}\nUsa /help para ver comandos.")

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def _cmd_help(self) -> None:
        await self._send(
            "<b>Prophet Engine Commands</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "/status — Engine health + uptime\n"
            "/pnl — PnL total, hoy, semana, win rate\n"
            "/strategies — Top 10 strategies por PnL\n"
            "/positions — Posiciones abiertas + exposicion\n"
            "/markets — Mercados por categoria\n"
            "/signals — Senales generadas hoy\n"
            "/help — Este mensaje"
        )

    async def _cmd_status(self) -> None:
        from prophet.db.database import get_session
        from prophet.db.models import SystemState
        from sqlalchemy import select

        uptime = ""
        if self._start_time:
            delta = datetime.now(timezone.utc) - self._start_time
            hours = int(delta.total_seconds() // 3600)
            mins = int((delta.total_seconds() % 3600) // 60)
            uptime = f"{hours}h {mins}m"

        async with get_session() as db:
            # Last scan time
            stmt = select(SystemState).where(SystemState.key == "last_full_scan")
            result = await db.execute(stmt)
            last_scan_row = result.scalar_one_or_none()
            last_scan = last_scan_row.value if last_scan_row else "never"

            # Last signal time
            stmt2 = select(SystemState).where(SystemState.key == "last_signal_run")
            result2 = await db.execute(stmt2)
            last_signal_row = result2.scalar_one_or_none()
            last_signal = last_signal_row.value if last_signal_row else "never"

        await self._send(
            "<b>Engine Status</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Uptime: {uptime}\n"
            f"Last scan: {last_scan}\n"
            f"Last signal run: {last_signal}\n"
            f"Bot: online"
        )

    async def _cmd_pnl(self) -> None:
        from prophet.db.database import get_session
        from prophet.db.models import Position
        from sqlalchemy import func, select

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=now.weekday())

        async with get_session() as db:
            # Total PnL
            total = (await db.execute(
                select(func.sum(Position.net_pnl)).where(Position.status == "closed")
            )).scalar_one_or_none() or 0.0

            # Today
            today = (await db.execute(
                select(func.sum(Position.net_pnl)).where(
                    Position.status == "closed", Position.closed_at >= today_start)
            )).scalar_one_or_none() or 0.0

            # This week
            week = (await db.execute(
                select(func.sum(Position.net_pnl)).where(
                    Position.status == "closed", Position.closed_at >= week_start)
            )).scalar_one_or_none() or 0.0

            # Win rate
            total_closed = (await db.execute(
                select(func.count()).select_from(Position).where(Position.status == "closed")
            )).scalar_one() or 0
            wins = (await db.execute(
                select(func.count()).select_from(Position).where(
                    Position.status == "closed", Position.net_pnl > 0)
            )).scalar_one() or 0
            wr = (wins / total_closed * 100) if total_closed > 0 else 0.0

            # Best single trade
            best = (await db.execute(
                select(Position.strategy, Position.net_pnl)
                .where(Position.status == "closed")
                .order_by(Position.net_pnl.desc()).limit(1)
            )).first()
            best_str = f"{best[0]}: +${best[1]:.2f}" if best and best[1] else "n/a"

            # Worst single trade
            worst = (await db.execute(
                select(Position.strategy, Position.net_pnl)
                .where(Position.status == "closed")
                .order_by(Position.net_pnl.asc()).limit(1)
            )).first()
            worst_str = f"{worst[0]}: ${worst[1]:.2f}" if worst and worst[1] else "n/a"

        s = lambda v: ("+" if v >= 0 else "") + f"${v:.2f}"
        await self._send(
            "<b>PnL Report</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Total: <b>{s(total)}</b>\n"
            f"Hoy: {s(today)}\n"
            f"Semana: {s(week)}\n"
            f"Win rate: {wr:.1f}% ({wins}/{total_closed})\n"
            f"Mejor trade: {best_str}\n"
            f"Peor trade: {worst_str}"
        )

    async def _cmd_strategies(self) -> None:
        from prophet.db.database import get_session
        from prophet.db.models import Position
        from sqlalchemy import func, select, case

        async with get_session() as db:
            stmt = (
                select(
                    Position.strategy,
                    func.sum(Position.net_pnl).label("pnl"),
                    func.count().label("trades"),
                    func.sum(case((Position.net_pnl > 0, 1), else_=0)).label("wins"),
                )
                .where(Position.status == "closed")
                .group_by(Position.strategy)
                .order_by(func.sum(Position.net_pnl).desc())
                .limit(10)
            )
            rows = (await db.execute(stmt)).all()

        if not rows:
            await self._send("No hay trades cerrados todavia.")
            return

        lines = []
        for i, row in enumerate(rows, 1):
            name, pnl, trades, w = row
            wr = (w / trades * 100) if trades > 0 else 0
            s = "+" if pnl >= 0 else ""
            emoji = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"{emoji} {i}. <code>{name}</code>\n"
                         f"    {s}${pnl:.2f} | {trades} trades | WR {wr:.0f}%")

        await self._send(
            "<b>Top 10 Strategies</b>\n"
            "━━━━━━━━━━━━━━━━━━\n" +
            "\n".join(lines)
        )

    async def _cmd_positions(self) -> None:
        from prophet.db.database import get_session
        from prophet.db.models import Position, Market
        from sqlalchemy import func, select

        async with get_session() as db:
            # Open positions count + exposure
            open_count = (await db.execute(
                select(func.count()).select_from(Position).where(Position.status == "open")
            )).scalar_one() or 0

            exposure = (await db.execute(
                select(func.sum(Position.size_usd)).where(Position.status == "open")
            )).scalar_one_or_none() or 0.0

            # By strategy
            by_strat = (await db.execute(
                select(Position.strategy, func.count(), func.sum(Position.size_usd))
                .where(Position.status == "open")
                .group_by(Position.strategy)
                .order_by(func.sum(Position.size_usd).desc())
                .limit(5)
            )).all()

            # Biggest positions
            biggest = (await db.execute(
                select(Position.strategy, Position.side, Position.size_usd, Position.entry_price,
                       Market.question)
                .join(Market, Market.id == Position.market_id)
                .where(Position.status == "open")
                .order_by(Position.size_usd.desc())
                .limit(5)
            )).all()

        strat_lines = ""
        for name, cnt, sz in by_strat:
            strat_lines += f"  <code>{name}</code>: {cnt} pos (${sz:.0f})\n"

        big_lines = ""
        for strat, side, sz, ep, q in biggest:
            big_lines += f"  {side} ${sz:.0f}@{ep:.3f} — {_escape((q or '?')[:50])}\n"

        await self._send(
            "<b>Open Positions</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Total: {open_count} positions\n"
            f"Exposure: ${exposure:.2f}\n\n"
            f"<b>By strategy:</b>\n{strat_lines}\n"
            f"<b>Biggest:</b>\n{big_lines}"
        )

    async def _cmd_markets(self) -> None:
        from prophet.db.database import get_session
        from prophet.db.models import Market
        from sqlalchemy import func, select

        async with get_session() as db:
            stmt = (
                select(
                    func.coalesce(Market.category, "unknown").label("cat"),
                    func.count().label("total"),
                    func.sum(case((Market.status == "active", 1), else_=0)).label("active"),
                )
                .group_by(func.coalesce(Market.category, "unknown"))
                .order_by(func.count().desc())
            )
            rows = (await db.execute(stmt)).all()

            total = (await db.execute(
                select(func.count()).select_from(Market)
            )).scalar_one() or 0

        lines = []
        for cat, cnt, active in rows:
            lines.append(f"  {cat}: {cnt} ({active} active)")

        await self._send(
            "<b>Markets by Category</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Total: {total}\n\n" +
            "\n".join(lines)
        )

    async def _cmd_signals(self) -> None:
        from prophet.db.database import get_session
        from prophet.db.models import Signal
        from sqlalchemy import func, select

        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        async with get_session() as db:
            # Total signals today
            total = (await db.execute(
                select(func.count()).select_from(Signal)
                .where(Signal.created_at >= today_start)
            )).scalar_one() or 0

            # By status
            by_status = (await db.execute(
                select(Signal.status, func.count())
                .where(Signal.created_at >= today_start)
                .group_by(Signal.status)
            )).all()

            # By strategy (top 5)
            by_strat = (await db.execute(
                select(Signal.strategy, func.count())
                .where(Signal.created_at >= today_start)
                .group_by(Signal.strategy)
                .order_by(func.count().desc())
                .limit(5)
            )).all()

        status_lines = ""
        for st, cnt in by_status:
            status_lines += f"  {st}: {cnt}\n"

        strat_lines = ""
        for name, cnt in by_strat:
            strat_lines += f"  <code>{name}</code>: {cnt}\n"

        await self._send(
            "<b>Signals Today</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Total: {total}\n\n"
            f"<b>By status:</b>\n{status_lines}\n"
            f"<b>Top strategies:</b>\n{strat_lines}"
        )

    # ------------------------------------------------------------------
    # Smart alerts (filtered, no spam)
    # ------------------------------------------------------------------

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
        """Only notify significant trades (wins > $5 or losses > $10)."""
        if _NOTIFY_LOSS_THRESHOLD < net_pnl < _NOTIFY_WIN_THRESHOLD:
            return  # skip small trades

        emoji = "✅" if net_pnl >= 0 else "🔴"
        pnl_sign = "+" if net_pnl >= 0 else ""
        text = (
            f"{emoji} <b>TRADE CLOSED</b>\n"
            f"<code>{strategy}</code>\n"
            f"{_escape(market_question[:80])}\n"
            f"{side} | {entry_price:.4f} → {exit_price:.4f}\n"
            f"PnL: <b>{pnl_sign}${net_pnl:.2f}</b> ({exit_reason})"
        )
        await self._send(text)

    async def notify_error(self, component: str, error_msg: str) -> None:
        """Alert on critical errors only."""
        text = (
            f"⚠️ <b>ERROR</b>\n"
            f"<code>{component}</code>\n"
            f"{_escape(error_msg[:300])}"
        )
        await self._send(text)

    # ------------------------------------------------------------------
    # Daily summary (scheduled at 20:00 UTC)
    # ------------------------------------------------------------------

    async def send_daily_summary(self, stats: dict[str, Any]) -> None:
        pnl = stats.get("total_pnl", 0)
        today = stats.get("today_pnl", 0)
        wr = stats.get("win_rate", 0) * 100

        strat_lines = ""
        for name, spnl in stats.get("top_strategies", [])[:5]:
            s = "+" if spnl >= 0 else ""
            strat_lines += f"  <code>{name}</code>: {s}${spnl:.2f}\n"

        s_total = ("+" if pnl >= 0 else "") + f"${pnl:.2f}"
        s_today = ("+" if today >= 0 else "") + f"${today:.2f}"

        await self._send(
            f"📊 <b>DAILY SUMMARY</b> — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Total PnL: <b>{s_total}</b>\n"
            f"Hoy: {s_today}\n"
            f"Win Rate: {wr:.1f}%\n"
            f"Open: {stats.get('open_positions', 0)} pos\n"
            f"Cerrados hoy: {stats.get('closed_today', 0)}\n"
            f"Exposicion: ${stats.get('total_exposure_usd', 0):.2f}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<b>Top Strategies:</b>\n{strat_lines}"
        )

    async def build_and_send_daily_summary(self) -> None:
        """Query DB and send the daily summary. Called by scheduler."""
        try:
            from prophet.db.database import get_session
            from prophet.db.models import Position
            from sqlalchemy import func, select, case

            now = datetime.now(timezone.utc)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

            async with get_session() as db:
                total_pnl = (await db.execute(
                    select(func.sum(Position.net_pnl)).where(Position.status == "closed")
                )).scalar_one_or_none() or 0.0

                today_pnl = (await db.execute(
                    select(func.sum(Position.net_pnl)).where(
                        Position.status == "closed", Position.closed_at >= today_start)
                )).scalar_one_or_none() or 0.0

                open_count = (await db.execute(
                    select(func.count()).select_from(Position).where(Position.status == "open")
                )).scalar_one() or 0

                closed_today = (await db.execute(
                    select(func.count()).select_from(Position).where(
                        Position.status == "closed", Position.closed_at >= today_start)
                )).scalar_one() or 0

                total_closed = (await db.execute(
                    select(func.count()).select_from(Position).where(Position.status == "closed")
                )).scalar_one() or 0
                wins = (await db.execute(
                    select(func.count()).select_from(Position).where(
                        Position.status == "closed", Position.net_pnl > 0)
                )).scalar_one() or 0
                win_rate = wins / total_closed if total_closed > 0 else 0.0

                exposure = (await db.execute(
                    select(func.sum(Position.size_usd)).where(Position.status == "open")
                )).scalar_one_or_none() or 0.0

                top_result = (await db.execute(
                    select(Position.strategy, func.sum(Position.net_pnl).label("pnl"))
                    .where(Position.status == "closed")
                    .group_by(Position.strategy)
                    .order_by(func.sum(Position.net_pnl).desc())
                    .limit(5)
                )).all()
                top_strategies = [(r[0], float(r[1])) for r in top_result]

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
