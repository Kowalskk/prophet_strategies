"""
Telegram Bot — on-demand commands via inline buttons + smart alerts.

Notifications (automatic, filtered)
------------------------------------
- Daily summary at 20:00 UTC
- Trade closed only if |PnL| > threshold
- Critical errors only

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

_NOTIFY_WIN_THRESHOLD = 5.0
_NOTIFY_LOSS_THRESHOLD = -10.0

# Main menu inline keyboard
_MAIN_MENU = {
    "inline_keyboard": [
        [
            {"text": "📊 PnL", "callback_data": "pnl"},
            {"text": "🔄 Status", "callback_data": "status"},
        ],
        [
            {"text": "📈 Strategies", "callback_data": "strategies"},
            {"text": "💼 Positions", "callback_data": "positions"},
        ],
        [
            {"text": "⏱ Countdown", "callback_data": "countdown"},
            {"text": "💰 Prices", "callback_data": "prices"},
        ],
        [
            {"text": "📡 Signals", "callback_data": "signals"},
            {"text": "🗺 Markets", "callback_data": "markets"},
        ],
    ]
}


class TelegramNotifier:
    """Async Telegram bot with inline button menu + smart alerts."""

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

    async def _send(
        self,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: dict | None = None,
    ) -> bool:
        if not self._enabled:
            return False
        if self._client is None:
            await self.start()

        url = f"{_BASE_URL.format(token=self.bot_token)}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            resp = await self._client.post(url, json=payload)  # type: ignore[union-attr]
            if resp.status_code != 200:
                logger.warning("Telegram send failed: %d %s", resp.status_code, resp.text[:200])
                return False
            return True
        except Exception as exc:
            logger.warning("Telegram send error: %s", exc)
            return False

    async def _answer_callback(self, callback_query_id: str) -> None:
        """Acknowledge the button tap so the loading spinner disappears."""
        if not self._enabled or self._client is None:
            return
        url = f"{_BASE_URL.format(token=self.bot_token)}/answerCallbackQuery"
        try:
            await self._client.post(url, json={"callback_query_id": callback_query_id})
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Command polling (messages + callback queries)
    # ------------------------------------------------------------------

    async def _poll_commands(self) -> None:
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

                    # Inline button tap
                    cbq = update.get("callback_query")
                    if cbq:
                        cid = str(cbq.get("message", {}).get("chat", {}).get("id", ""))
                        if cid == self.chat_id:
                            await self._answer_callback(cbq["id"])
                            await self._handle_action(cbq.get("data", ""))
                        continue

                    # Text message / command
                    msg = update.get("message", {})
                    text = msg.get("text", "")
                    cid = str(msg.get("chat", {}).get("id", ""))
                    if cid != self.chat_id:
                        continue

                    if text.startswith("/"):
                        cmd = text.split()[0].lower().split("@")[0]
                        # Map slash commands to action names
                        action = cmd.lstrip("/")
                        if action in ("start", "help", "menu"):
                            await self._cmd_menu()
                        else:
                            await self._handle_action(action)

            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.debug("Telegram poll error: %s", exc)
                await asyncio.sleep(10)

    async def _handle_action(self, action: str) -> None:
        handlers = {
            "status": self._cmd_status,
            "pnl": self._cmd_pnl,
            "strategies": self._cmd_strategies,
            "positions": self._cmd_positions,
            "markets": self._cmd_markets,
            "signals": self._cmd_signals,
            "countdown": self._cmd_countdown,
            "prices": self._cmd_prices,
            "menu": self._cmd_menu,
        }
        handler = handlers.get(action)
        if handler:
            try:
                await handler()
            except Exception as exc:
                await self._send(f"Error en {action}: {_escape(str(exc)[:200])}")
        else:
            await self._cmd_menu()

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def _cmd_menu(self) -> None:
        await self._send(
            "<b>Prophet Engine</b> — elige una opcion:",
            reply_markup=_MAIN_MENU,
        )

    async def _cmd_status(self) -> None:
        from prophet.db.database import get_session
        from prophet.db.models import Market, Position, Signal, OrderBookSnapshot
        from sqlalchemy import func, select

        uptime = "?"
        if self._start_time:
            delta = datetime.now(timezone.utc) - self._start_time
            h = int(delta.total_seconds() // 3600)
            m = int((delta.total_seconds() % 3600) // 60)
            uptime = f"{h}h {m}m"

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        async with get_session() as db:
            active_markets = (await db.execute(
                select(func.count()).select_from(Market).where(Market.status == "active")
            )).scalar_one() or 0

            open_positions = (await db.execute(
                select(func.count()).select_from(Position).where(Position.status == "open")
            )).scalar_one() or 0

            signals_today = (await db.execute(
                select(func.count()).select_from(Signal).where(Signal.created_at >= today_start)
            )).scalar_one() or 0

            last_ob = (await db.execute(
                select(func.max(OrderBookSnapshot.timestamp))
            )).scalar_one_or_none()
            last_data = str(last_ob)[:19] if last_ob else "never"

        await self._send(
            "<b>🔄 Engine Status</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Uptime: {uptime}\n"
            f"Mercados activos: {active_markets:,}\n"
            f"Posiciones abiertas: {open_positions:,}\n"
            f"Señales hoy: {signals_today:,}\n"
            f"Última data: {last_data} UTC\n"
            f"Bot: 🟢 online",
            reply_markup=_MAIN_MENU,
        )

    async def _cmd_pnl(self) -> None:
        from prophet.db.database import get_session
        from prophet.db.models import Position
        from sqlalchemy import func, select

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=now.weekday())

        async with get_session() as db:
            total = (await db.execute(
                select(func.sum(Position.net_pnl)).where(Position.status == "closed")
            )).scalar_one_or_none() or 0.0

            today = (await db.execute(
                select(func.sum(Position.net_pnl)).where(
                    Position.status == "closed", Position.closed_at >= today_start)
            )).scalar_one_or_none() or 0.0

            week = (await db.execute(
                select(func.sum(Position.net_pnl)).where(
                    Position.status == "closed", Position.closed_at >= week_start)
            )).scalar_one_or_none() or 0.0

            total_closed = (await db.execute(
                select(func.count()).select_from(Position).where(Position.status == "closed")
            )).scalar_one() or 0

            wins = (await db.execute(
                select(func.count()).select_from(Position).where(
                    Position.status == "closed", Position.net_pnl > 0)
            )).scalar_one() or 0

            wr = (wins / total_closed * 100) if total_closed > 0 else 0.0

            best = (await db.execute(
                select(Position.strategy, Position.net_pnl)
                .where(Position.status == "closed")
                .order_by(Position.net_pnl.desc()).limit(1)
            )).first()

            worst = (await db.execute(
                select(Position.strategy, Position.net_pnl)
                .where(Position.status == "closed")
                .order_by(Position.net_pnl.asc()).limit(1)
            )).first()

        def _s(v: float) -> str:
            return ("+" if v >= 0 else "") + f"${v:.2f}"

        best_str = f"{best[0]}: +${best[1]:.2f}" if best and best[1] else "n/a"
        worst_str = f"{worst[0]}: ${worst[1]:.2f}" if worst and worst[1] else "n/a"

        await self._send(
            "<b>📊 PnL Report</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Total: <b>{_s(total)}</b>\n"
            f"Hoy: {_s(today)}\n"
            f"Semana: {_s(week)}\n"
            f"Win rate: {wr:.1f}% ({wins}/{total_closed})\n"
            f"Mejor trade: {_escape(best_str)}\n"
            f"Peor trade: {_escape(worst_str)}",
            reply_markup=_MAIN_MENU,
        )

    async def _cmd_strategies(self) -> None:
        from prophet.db.database import get_session
        from prophet.db.models import Position
        from sqlalchemy import func, select, case

        async with get_session() as db:
            # Top 10 by closed PnL
            rows = (await db.execute(
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
            )).all()

            # Open positions by strategy family
            open_by_family = (await db.execute(
                select(Position.strategy, func.count().label("cnt"))
                .where(Position.status == "open")
                .group_by(Position.strategy)
                .order_by(func.count().desc())
                .limit(8)
            )).all()

        if not rows:
            await self._send(
                "<b>📈 Strategies</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "No hay trades cerrados todavia.\n\n"
                "<i>Estrategias activas: SRB (cheap/mid + 12h/24h/48h), CSRB, VS, Political, Weather</i>",
                reply_markup=_MAIN_MENU,
            )
            return

        lines = []
        for i, (name, pnl, trades, w) in enumerate(rows, 1):
            wr = (w / trades * 100) if trades > 0 else 0
            s = "+" if pnl >= 0 else ""
            emoji = "🟢" if pnl >= 0 else "🔴"
            lines.append(
                f"{emoji} {i}. <code>{name}</code>\n"
                f"   {s}${pnl:.2f} | {trades}t | {wr:.0f}% WR"
            )

        open_lines = ""
        if open_by_family:
            open_lines = "\n<b>Abiertas por estrategia:</b>\n"
            for name, cnt in open_by_family:
                open_lines += f"  <code>{name}</code>: {cnt}\n"

        await self._send(
            "<b>📈 Top 10 Strategies (closed)</b>\n"
            "━━━━━━━━━━━━━━━━━━\n" +
            "\n".join(lines) +
            open_lines,
            reply_markup=_MAIN_MENU,
        )

    async def _cmd_positions(self) -> None:
        from prophet.db.database import get_session
        from prophet.db.models import Position, Market
        from sqlalchemy import func, select

        async with get_session() as db:
            open_count = (await db.execute(
                select(func.count()).select_from(Position).where(Position.status == "open")
            )).scalar_one() or 0

            exposure = (await db.execute(
                select(func.sum(Position.size_usd)).where(Position.status == "open")
            )).scalar_one_or_none() or 0.0

            by_strat = (await db.execute(
                select(Position.strategy, func.count(), func.sum(Position.size_usd))
                .where(Position.status == "open")
                .group_by(Position.strategy)
                .order_by(func.count().desc())
                .limit(8)
            )).all()

            biggest = (await db.execute(
                select(Position.strategy, Position.side, Position.size_usd,
                       Position.entry_price, Market.question)
                .join(Market, Market.id == Position.market_id)
                .where(Position.status == "open")
                .order_by(Position.size_usd.desc())
                .limit(5)
            )).all()

        strat_lines = ""
        for name, cnt, sz in by_strat:
            strat_lines += f"  <code>{name}</code>: {cnt} (${sz:.0f})\n"

        big_lines = ""
        for strat, side, sz, ep, q in biggest:
            big_lines += f"  {side} ${sz:.0f}@{ep:.3f} — {_escape((q or '?')[:45])}\n"

        await self._send(
            "<b>💼 Open Positions</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Total: <b>{open_count:,}</b> posiciones\n"
            f"Exposicion: <b>${exposure:,.2f}</b>\n\n"
            f"<b>Por estrategia:</b>\n{strat_lines}\n"
            f"<b>Mayores posiciones:</b>\n{big_lines}",
            reply_markup=_MAIN_MENU,
        )

    async def _cmd_markets(self) -> None:
        from prophet.db.database import get_session
        from prophet.db.models import Market
        from sqlalchemy import func, select, text

        async with get_session() as db:
            rows = (await db.execute(text(
                "SELECT coalesce(category,'unknown') as cat, "
                "count(*) as total, "
                "sum(case when status='active' then 1 else 0 end) as active "
                "FROM markets GROUP BY coalesce(category,'unknown') ORDER BY count(*) DESC"
            ))).all()

            total = (await db.execute(
                select(func.count()).select_from(Market)
            )).scalar_one() or 0

        lines = [f"  {cat}: {cnt:,} ({active or 0} activos)" for cat, cnt, active in rows]

        await self._send(
            "<b>🗺 Markets by Category</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Total: {total:,}\n\n" +
            "\n".join(lines),
            reply_markup=_MAIN_MENU,
        )

    async def _cmd_signals(self) -> None:
        from prophet.db.database import get_session
        from prophet.db.models import Signal
        from sqlalchemy import func, select

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        async with get_session() as db:
            total = (await db.execute(
                select(func.count()).select_from(Signal).where(Signal.created_at >= today_start)
            )).scalar_one() or 0

            by_status = (await db.execute(
                select(Signal.status, func.count())
                .where(Signal.created_at >= today_start)
                .group_by(Signal.status)
            )).all()

            by_strat = (await db.execute(
                select(Signal.strategy, func.count())
                .where(Signal.created_at >= today_start)
                .group_by(Signal.strategy)
                .order_by(func.count().desc())
                .limit(8)
            )).all()

        status_lines = "".join(f"  {st}: {cnt}\n" for st, cnt in by_status)
        strat_lines = "".join(f"  <code>{name}</code>: {cnt}\n" for name, cnt in by_strat)

        await self._send(
            "<b>📡 Signals Today</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Total: <b>{total:,}</b>\n\n"
            f"<b>Por estado:</b>\n{status_lines}\n"
            f"<b>Top estrategias:</b>\n{strat_lines}",
            reply_markup=_MAIN_MENU,
        )

    async def _cmd_countdown(self) -> None:
        from prophet.db.database import get_session
        from sqlalchemy import text

        async with get_session() as db:
            rows = (await db.execute(text("""
                SELECT
                    m.resolution_date,
                    COUNT(DISTINCT m.id)              AS markets,
                    COUNT(p.id)                       AS positions,
                    ROUND(SUM(p.size_usd)::numeric,2) AS capital
                FROM positions p
                JOIN markets m ON p.market_id = m.id
                WHERE p.status = 'open'
                  AND m.resolution_date IS NOT NULL
                GROUP BY m.resolution_date
                ORDER BY m.resolution_date ASC
            """))).all()

        if not rows:
            await self._send(
                "No hay posiciones abiertas con fecha de resolución.",
                reply_markup=_MAIN_MENU,
            )
            return

        from collections import defaultdict
        import calendar

        now = datetime.now(timezone.utc)
        today = now.date()

        # Buckets: expired, today, this_week (next 6 days), by_month (month label → aggregate)
        expired = []
        day_rows = []   # (res_date, markets, positions, capital, secs)
        month_buckets: dict[str, dict] = defaultdict(lambda: {"mkts": 0, "pos": 0, "cap": 0.0, "days": 9999})

        for res_date, markets, positions, capital in rows:
            target = datetime(res_date.year, res_date.month, res_date.day,
                              23, 59, 59, tzinfo=timezone.utc)
            secs = int((target - now).total_seconds())
            days = max(secs // 86400, 0)

            if secs < 0:
                expired.append((res_date, markets, positions, float(capital)))
            elif days <= 6:
                day_rows.append((res_date, markets, positions, float(capital), secs))
            else:
                # Group by "Mon YYYY" or just "Mon" if same year
                label = res_date.strftime("%b %Y") if res_date.year != today.year else res_date.strftime("%b %Y")
                month_buckets[label]["mkts"] += markets
                month_buckets[label]["pos"] += positions
                month_buckets[label]["cap"] += float(capital)
                month_buckets[label]["days"] = min(month_buckets[label]["days"], days)

        lines = []

        # Expired
        if expired:
            tot_m = sum(r[1] for r in expired)
            tot_p = sum(r[2] for r in expired)
            tot_c = sum(r[3] for r in expired)
            lines.append(f"⚠️ <b>EXPIRADO</b> — {tot_m} mkt | {tot_p} pos | ${tot_c:,.0f}")

        # Day-by-day for next 7 days
        week_total_pos = 0
        week_total_cap = 0.0
        for res_date, markets, positions, capital, secs in day_rows:
            hours = (secs % 86400) // 3600
            mins = (secs % 3600) // 60
            if secs // 86400 == 0:
                label = f"🔥 <b>HOY {res_date.strftime('%b %d')}</b> — {hours:02d}h {mins:02d}m"
            else:
                d = secs // 86400
                label = f"⏳ <b>{res_date.strftime('%b %d')}</b> — {d}d {hours:02d}h"
            lines.append(f"{label}\n   {markets} mkt | {positions} pos | ${capital:,.0f}")
            week_total_pos += positions
            week_total_cap += capital

        # This-week summary line
        if day_rows:
            lines.append(f"<b>→ Esta semana: {week_total_pos:,} pos | ${week_total_cap:,.0f}</b>")

        # Monthly buckets
        if month_buckets:
            lines.append("")
            for label, b in month_buckets.items():
                lines.append(f"📅 <b>{label}</b> — {b['days']}d+\n   {b['mkts']} mkt | {b['pos']:,} pos | ${b['cap']:,.0f}")

        total_pos = sum(r[2] for r in rows)
        total_cap = sum(float(r[3]) for r in rows)

        await self._send(
            "<b>⏱ Resoluciones</b>\n"
            "━━━━━━━━━━━━━━━━━━\n" +
            "\n".join(lines) +
            f"\n━━━━━━━━━━━━━━━━━━\n"
            f"Total: {total_pos:,} pos | ${total_cap:,.0f}",
            reply_markup=_MAIN_MENU,
        )

    async def _cmd_prices(self) -> None:
        from prophet.db.database import get_session
        from sqlalchemy import text

        async with get_session() as db:
            rows = (await db.execute(text(
                "SELECT crypto, price_usd, timestamp "
                "FROM price_snapshots "
                "WHERE (crypto, timestamp) IN ("
                "  SELECT crypto, MAX(timestamp) FROM price_snapshots GROUP BY crypto"
                ") "
                "ORDER BY crypto"
            ))).all()

        if not rows:
            await self._send("No hay datos de precios disponibles.", reply_markup=_MAIN_MENU)
            return

        lines = []
        for crypto, price, ts in rows:
            lines.append(f"  <b>{crypto}</b>: ${price:,.2f} <i>({str(ts)[:16]})</i>")

        await self._send(
            "<b>💰 Crypto Prices</b>\n"
            "━━━━━━━━━━━━━━━━━━\n" +
            "\n".join(lines),
            reply_markup=_MAIN_MENU,
        )

    # ------------------------------------------------------------------
    # Smart alerts
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
        market_url: str = "",
    ) -> None:
        """Only notify significant trades."""
        if _NOTIFY_LOSS_THRESHOLD < net_pnl < _NOTIFY_WIN_THRESHOLD:
            return

        emoji = "✅" if net_pnl >= 0 else "🔴"
        pnl_sign = "+" if net_pnl >= 0 else ""
        market_line = _escape(market_question[:80])
        if market_url:
            market_line = f'<a href="{market_url}">{market_line}</a>'

        await self._send(
            f"{emoji} <b>TRADE CLOSED</b>\n"
            f"<code>{_escape(strategy)}</code>\n"
            f"{market_line}\n"
            f"{side} | {entry_price:.4f} → {exit_price:.4f}\n"
            f"PnL: <b>{pnl_sign}${net_pnl:.2f}</b> ({_escape(exit_reason)})"
        )

    async def notify_error(self, component: str, error_msg: str) -> None:
        await self._send(
            f"⚠️ <b>ERROR</b>\n"
            f"<code>{_escape(component)}</code>\n"
            f"{_escape(error_msg[:300])}"
        )

    # ------------------------------------------------------------------
    # Daily summary (called by scheduler at 20:00 UTC)
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
            f"Abiertas: {stats.get('open_positions', 0):,} pos\n"
            f"Cerradas hoy: {stats.get('closed_today', 0):,}\n"
            f"Exposición: ${stats.get('total_exposure_usd', 0):,.2f}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<b>Top Strategies:</b>\n{strat_lines}",
            reply_markup=_MAIN_MENU,
        )

    async def build_and_send_daily_summary(self) -> None:
        """Query DB and send the daily summary. Called by scheduler."""
        try:
            from prophet.db.database import get_session
            from prophet.db.models import Position
            from sqlalchemy import func, select

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

            await self.send_daily_summary({
                "total_pnl": total_pnl,
                "today_pnl": today_pnl,
                "open_positions": open_count,
                "closed_today": closed_today,
                "win_rate": win_rate,
                "top_strategies": [(r[0], float(r[1])) for r in top_result],
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
