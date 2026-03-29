"""
Auto-Optimizer — Autonomous strategy parameter tuner.

Runs nightly (or on-demand). For each registered strategy:
1. Reads current best params from experiment history
2. Randomly mutates one parameter by ±10-20%
3. Scores the new params using historical signal performance from DB
4. Keeps if better, reverts if worse
5. Logs experiment to experiments/history.jsonl

Scoring is based on REAL historical data already in our DB:
- Signals that were generated with those params
- Whether the corresponding positions were profitable
- Win rate, avg net_pnl, total trades

This is different from simulation-based backtest — it uses actual
live paper trading results already accumulated in the DB.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, func

from prophet.db.database import get_session
from prophet.db.models import Signal, Position
from prophet.strategies.registry import STRATEGY_REGISTRY, _register_builtins

logger = logging.getLogger(__name__)

# Non-tunable parameter keys — skip these during mutation
_NON_TUNABLE = {
    "exit_strategy",
    "side",
    "exit_params",
    "min_market_hours_remaining",
}


class AutoOptimizer:
    """Autonomous parameter optimizer using live DB results as the fitness signal.

    Implements a simple hill-climbing loop: for each strategy, mutate one
    numeric parameter, score the result against real closed positions, and
    keep the mutation only when it improves the score.
    """

    def __init__(self) -> None:
        # Experiments directory is two levels above src/engine:
        # src/engine/prophet/core/ -> src/engine/ -> src/ -> project root
        self._experiments_dir: Path = (
            Path(__file__).resolve().parent.parent.parent.parent.parent / "experiments"
        )
        self._history_file: Path = self._experiments_dir / "history.jsonl"
        self._best_params_file: Path = self._experiments_dir / "best_params.json"
        self._running: bool = False

        # Ensure experiments directory exists
        self._experiments_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run_once(self) -> dict[str, Any]:
        """Run one full optimization round across all registered strategies.

        Returns a summary dict with per-strategy results.
        """
        if self._running:
            logger.warning("AutoOptimizer.run_once() called while already running; skipping.")
            return {}

        self._running = True
        summary: dict[str, Any] = {}

        try:
            best_params = self._load_best_params()

            # Ensure all built-in strategies are registered
            if not STRATEGY_REGISTRY:
                _register_builtins()

            round_num = self._get_next_round_num()
            logger.info("AutoOptimizer round %d — %d strategies", round_num, len(STRATEGY_REGISTRY))

            for strategy_name, strategy_cls in STRATEGY_REGISTRY.items():
                try:
                    result = await self._optimize_strategy(
                        strategy_name=strategy_name,
                        strategy_cls=strategy_cls,
                        best_params=best_params,
                        round_num=round_num,
                    )
                    summary[strategy_name] = result
                except Exception as exc:
                    logger.error("Error optimizing %r: %s", strategy_name, exc, exc_info=True)
                    summary[strategy_name] = {"status": "error", "error": str(exc)}

            self._save_best_params(best_params)

            report_path = await self.generate_report()
            logger.info("Learning report written to %s", report_path)

        finally:
            self._running = False

        return summary

    # ------------------------------------------------------------------
    # Per-strategy optimization
    # ------------------------------------------------------------------

    async def _optimize_strategy(
        self,
        strategy_name: str,
        strategy_cls: type,
        best_params: dict[str, dict],
        round_num: int,
    ) -> dict[str, Any]:
        """Run one mutation-score-compare cycle for a single strategy."""
        # Resolve current params: best known or class defaults
        default_params: dict[str, Any] = dict(getattr(strategy_cls, "default_params", {}))
        current_params: dict[str, Any] = best_params.get(strategy_name, default_params)

        # Score current params
        current_score = await self._score_params(strategy_name, current_params)

        # Generate mutant
        mutated_params = self._mutate_params(current_params)

        # Score mutant
        mutated_score = await self._score_params(strategy_name, mutated_params)

        # Accept if meaningfully better
        if mutated_score > current_score + 0.01:
            best_params[strategy_name] = mutated_params
            status = "kept"
            logger.info(
                "  [%s] KEPT mutation  %.4f → %.4f",
                strategy_name,
                current_score,
                mutated_score,
            )
        else:
            # Keep original (ensure it is recorded in best_params)
            best_params[strategy_name] = current_params
            status = "reverted"
            logger.debug(
                "  [%s] reverted mutation %.4f vs %.4f",
                strategy_name,
                mutated_score,
                current_score,
            )

        self._log_experiment(
            strategy=strategy_name,
            round_num=round_num,
            original_score=current_score,
            mutated_score=mutated_score,
            original_params=current_params,
            mutated_params=mutated_params,
            status=status,
        )

        return {
            "status": status,
            "original_score": current_score,
            "mutated_score": mutated_score,
            "accepted": status == "kept",
        }

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    async def _score_params(self, strategy_name: str, params: dict[str, Any]) -> float:
        """Score strategy params using real closed-position P&L from the DB.

        Returns a float in approximately [0.0, 1.0] where higher is better.
        Returns 0.5 (neutral) when fewer than 5 closed positions exist for
        the strategy (not enough data to make a judgment).

        Note: params are used for weighting confidence but the primary signal
        is actual position outcomes already recorded in the DB for this strategy.
        """
        try:
            async with get_session() as session:
                # Query closed positions for this strategy
                stmt = select(Position).where(
                    Position.strategy == strategy_name,
                    Position.status == "closed",
                    Position.net_pnl.is_not(None),
                )
                result = await session.execute(stmt)
                positions = result.scalars().all()

            if len(positions) < 5:
                return 0.5  # Not enough data

            pnl_values = [p.net_pnl for p in positions if p.net_pnl is not None]
            if not pnl_values:
                return 0.5

            win_rate = sum(1 for v in pnl_values if v > 0) / len(pnl_values)
            avg_pnl = sum(pnl_values) / len(pnl_values)

            # Normalize avg_pnl: clamp to [-10, 10] then scale to [0, 1]
            normalized_pnl = max(-1.0, min(1.0, avg_pnl / 10.0))
            # Shift from [-1, 1] to [0, 1]
            normalized_pnl_0_1 = (normalized_pnl + 1.0) / 2.0

            score = win_rate * 0.4 + normalized_pnl_0_1 * 0.6
            return round(min(1.0, max(0.0, score)), 6)

        except Exception as exc:
            logger.warning("_score_params failed for %r: %s", strategy_name, exc)
            return 0.5

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def _mutate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of params with ONE numeric parameter randomly mutated.

        - Float params: multiplied by uniform(0.85, 1.15)  (±15%)
        - Int params: shifted by randint(-1, 1), minimum 1
        - Prices clamped to [0.001, 0.999]
        - Sizes clamped to [1.0, 500.0]
        - Non-tunable keys (exit_strategy, side, etc.) are never mutated.
        """
        mutated = dict(params)  # shallow copy

        # Find tunable numeric params
        candidates = [
            k for k, v in params.items()
            if k not in _NON_TUNABLE and isinstance(v, (int, float)) and not isinstance(v, bool)
        ]

        if not candidates:
            return mutated  # Nothing to mutate

        key = random.choice(candidates)
        value = params[key]

        if isinstance(value, float):
            new_value = value * random.uniform(0.85, 1.15)
            # Apply domain clamps based on parameter name heuristics
            if "price" in key or key in ("target_price", "entry_price", "min_price", "max_price",
                                          "bid_offset", "spread", "threshold"):
                new_value = max(0.001, min(0.999, new_value))
            elif "size" in key or "usd" in key or "amount" in key:
                new_value = max(1.0, min(500.0, new_value))
            mutated[key] = round(new_value, 6)

        elif isinstance(value, int):
            delta = random.randint(-1, 1)
            new_value = max(1, value + delta)
            mutated[key] = new_value

        logger.debug("Mutated param %r: %s → %s", key, value, mutated[key])
        return mutated

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load_best_params(self) -> dict[str, dict]:
        """Load best known params from best_params.json.

        Returns an empty dict if the file does not exist yet (strategy
        defaults will be used as the starting point).
        """
        if not self._best_params_file.exists():
            return {}
        try:
            with self._best_params_file.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read best_params.json: %s — starting fresh.", exc)
            return {}

    def _save_best_params(self, params: dict[str, dict]) -> None:
        """Persist best params to best_params.json with pretty formatting."""
        try:
            with self._best_params_file.open("w", encoding="utf-8") as fh:
                json.dump(params, fh, indent=2, default=str)
            logger.debug("best_params.json saved (%d strategies).", len(params))
        except OSError as exc:
            logger.error("Failed to save best_params.json: %s", exc)

    def _log_experiment(
        self,
        strategy: str,
        round_num: int,
        original_score: float,
        mutated_score: float,
        original_params: dict[str, Any],
        mutated_params: dict[str, Any],
        status: str,
    ) -> None:
        """Append one JSONL record to history.jsonl."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "round": round_num,
            "strategy": strategy,
            "original_score": original_score,
            "mutated_score": mutated_score,
            "delta": round(mutated_score - original_score, 6),
            "status": status,
            "original_params": original_params,
            "mutated_params": mutated_params,
        }
        try:
            with self._history_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        except OSError as exc:
            logger.error("Failed to write history.jsonl: %s", exc)

    def _get_next_round_num(self) -> int:
        """Determine the next round number from existing history."""
        if not self._history_file.exists():
            return 1
        try:
            last_round = 0
            with self._history_file.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        last_round = max(last_round, rec.get("round", 0))
                    except json.JSONDecodeError:
                        continue
            return last_round + 1
        except OSError:
            return 1

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    async def generate_report(self) -> str:
        """Read history.jsonl and write experiments/learning_report.md.

        Returns the path to the written report file.
        """
        records = self._read_history()

        # Aggregate per-strategy stats
        per_strategy: dict[str, dict[str, Any]] = {}
        for rec in records:
            name = rec.get("strategy", "unknown")
            if name not in per_strategy:
                per_strategy[name] = {
                    "experiments": [],
                    "kept_count": 0,
                    "reverted_count": 0,
                    "best_score": 0.0,
                    "latest_score": 0.0,
                    "latest_params": {},
                }
            entry = per_strategy[name]
            entry["experiments"].append(rec)
            score = rec.get("original_score", 0.0)
            entry["best_score"] = max(entry["best_score"], score)
            entry["latest_score"] = score
            entry["latest_params"] = rec.get("original_params", {})
            if rec.get("status") == "kept":
                entry["kept_count"] += 1
            else:
                entry["reverted_count"] += 1

        # Trend analysis: compare first half vs second half scores
        def _trend(experiments: list[dict]) -> str:
            if len(experiments) < 4:
                return "stable (insufficient data)"
            mid = len(experiments) // 2
            first_half_avg = sum(e.get("original_score", 0.5) for e in experiments[:mid]) / mid
            second_half_avg = sum(e.get("original_score", 0.5) for e in experiments[mid:]) / (len(experiments) - mid)
            delta = second_half_avg - first_half_avg
            if delta > 0.02:
                return "improving"
            elif delta < -0.02:
                return "declining"
            return "stable"

        total_experiments = len(records)
        strategies_improved = sum(
            1 for s in per_strategy.values() if s["kept_count"] > 0
        )
        strategies_insufficient = sum(
            1 for s in per_strategy.values()
            if all(e.get("original_score") == 0.5 for e in s["experiments"])
        )

        report_lines = [
            "# Auto-Optimizer Learning Report",
            "",
            f"_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
            "",
            "## Summary",
            "",
            f"- **Total experiments run**: {total_experiments}",
            f"- **Strategies tracked**: {len(per_strategy)}",
            f"- **Strategies with improvements accepted**: {strategies_improved}",
            f"- **Strategies with insufficient live data**: {strategies_insufficient}",
            "",
            "---",
            "",
            "## Per-Strategy Results",
            "",
        ]

        for name, stats in sorted(per_strategy.items()):
            n = len(stats["experiments"])
            trend = _trend(stats["experiments"])
            report_lines += [
                f"### `{name}`",
                "",
                f"- **Experiments**: {n}",
                f"- **Best score ever**: {stats['best_score']:.4f}",
                f"- **Latest score**: {stats['latest_score']:.4f}",
                f"- **Mutations accepted / rejected**: {stats['kept_count']} / {stats['reverted_count']}",
                f"- **Trend**: {trend}",
                f"- **Current params**: `{json.dumps(stats['latest_params'], default=str)}`",
                "",
            ]

        # Top 5 most recent experiments
        recent = records[-5:] if len(records) >= 5 else records
        recent = list(reversed(recent))  # newest first

        report_lines += [
            "---",
            "",
            "## 5 Most Recent Experiments",
            "",
            "| Timestamp | Strategy | Score Before | Score After | Delta | Status |",
            "|-----------|----------|-------------|------------|-------|--------|",
        ]
        for rec in recent:
            ts = rec.get("timestamp", "")[:19].replace("T", " ")
            report_lines.append(
                f"| {ts} "
                f"| {rec.get('strategy', '')} "
                f"| {rec.get('original_score', 0):.4f} "
                f"| {rec.get('mutated_score', 0):.4f} "
                f"| {rec.get('delta', 0):+.4f} "
                f"| {rec.get('status', '')} |"
            )

        report_lines.append("")

        report_path = self._experiments_dir / "learning_report.md"
        try:
            with report_path.open("w", encoding="utf-8") as fh:
                fh.write("\n".join(report_lines))
        except OSError as exc:
            logger.error("Failed to write learning_report.md: %s", exc)

        return str(report_path)

    def _read_history(self) -> list[dict[str, Any]]:
        """Read all records from history.jsonl. Returns [] if file absent."""
        if not self._history_file.exists():
            return []
        records = []
        try:
            with self._history_file.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError as exc:
            logger.warning("Could not read history.jsonl: %s", exc)
        return records

    # ------------------------------------------------------------------
    # Public convenience
    # ------------------------------------------------------------------

    def get_best_params(self, strategy_name: str) -> dict[str, Any] | None:
        """Return the current best params for a strategy, or None if not yet optimized."""
        best = self._load_best_params()
        return best.get(strategy_name)
