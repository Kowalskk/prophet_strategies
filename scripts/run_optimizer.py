#!/usr/bin/env python3
"""
Run the auto-optimizer manually or via cron.

Usage:
    python scripts/run_optimizer.py              # run once
    python scripts/run_optimizer.py --rounds 5   # run 5 optimization rounds
    python scripts/run_optimizer.py --report     # just generate the report

Cron (nightly at 2am VPS time):
    0 2 * * * cd /root/prophet_strategies && python scripts/run_optimizer.py >> /var/log/optimizer.log 2>&1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — must happen before any prophet imports
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPTS_DIR.parent
_ENGINE_SRC = _PROJECT_ROOT / "src" / "engine"

sys.path.insert(0, str(_ENGINE_SRC))

# Ensure experiments directory exists at project root level
_EXPERIMENTS_DIR = _PROJECT_ROOT / "experiments"
_EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)

# Create .gitkeep so the directory is tracked by git
_gitkeep = _EXPERIMENTS_DIR / ".gitkeep"
if not _gitkeep.exists():
    _gitkeep.touch()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run_optimizer")


# ---------------------------------------------------------------------------
# Main async logic
# ---------------------------------------------------------------------------

async def _run(rounds: int) -> None:
    """Run the optimizer for the requested number of rounds."""
    from prophet.core.auto_optimizer import AutoOptimizer

    optimizer = AutoOptimizer()

    all_summaries: list[dict] = []

    for round_idx in range(1, rounds + 1):
        logger.info("=== Optimization round %d / %d ===", round_idx, rounds)
        summary = await optimizer.run_once()
        all_summaries.append(summary)

        # Print per-round summary
        kept = sum(1 for s in summary.values() if s.get("status") == "kept")
        reverted = sum(1 for s in summary.values() if s.get("status") == "reverted")
        errors = sum(1 for s in summary.values() if s.get("status") == "error")
        logger.info(
            "  Round %d complete — kept: %d  reverted: %d  errors: %d",
            round_idx, kept, reverted, errors,
        )

    # Final summary
    print("\n" + "=" * 60)
    print(f"AUTO-OPTIMIZER COMPLETE  ({rounds} round(s))")
    print("=" * 60)

    total_kept = 0
    total_reverted = 0
    total_errors = 0

    for round_summary in all_summaries:
        for strategy_name, result in round_summary.items():
            status = result.get("status", "unknown")
            if status == "kept":
                total_kept += 1
            elif status == "reverted":
                total_reverted += 1
            elif status == "error":
                total_errors += 1

    print(f"  Mutations accepted   : {total_kept}")
    print(f"  Mutations rejected   : {total_reverted}")
    print(f"  Errors               : {total_errors}")
    print(f"  Experiments dir      : {_EXPERIMENTS_DIR}")
    print(f"  Best params file     : {_EXPERIMENTS_DIR / 'best_params.json'}")
    print(f"  History file         : {_EXPERIMENTS_DIR / 'history.jsonl'}")
    print(f"  Learning report      : {_EXPERIMENTS_DIR / 'learning_report.md'}")
    print("=" * 60 + "\n")

    # Print per-strategy final state if only one round (keep output manageable)
    if rounds == 1 and all_summaries:
        print("Per-strategy results:")
        for strategy_name, result in sorted(all_summaries[0].items()):
            status = result.get("status", "?")
            orig = result.get("original_score", 0.0)
            mut = result.get("mutated_score", 0.0)
            delta = mut - orig
            marker = "+" if status == "kept" else ("-" if status == "reverted" else "!")
            print(f"  [{marker}] {strategy_name:<35}  score: {orig:.4f} → {mut:.4f}  ({delta:+.4f})")
        print()


async def _report_only() -> None:
    """Generate and print the learning report without running optimization."""
    from prophet.core.auto_optimizer import AutoOptimizer

    optimizer = AutoOptimizer()
    report_path = await optimizer.generate_report()

    report_file = Path(report_path)
    if report_file.exists():
        print(report_file.read_text(encoding="utf-8"))
    else:
        print(f"Report written to: {report_path}")


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prophet Auto-Optimizer — autonomous strategy parameter tuner.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=1,
        metavar="N",
        help="Number of optimization rounds to run (default: 1).",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Only generate and print the learning report; skip optimization.",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        metavar="URL",
        help=(
            "Override the DATABASE_URL environment variable. "
            "Format: postgresql+asyncpg://user:pass@host:5432/db"
        ),
    )
    args = parser.parse_args()

    # Allow DATABASE_URL override via CLI flag
    if args.db_url:
        os.environ["DATABASE_URL"] = args.db_url
        logger.info("DATABASE_URL overridden via --db-url flag.")

    if args.report:
        logger.info("Report-only mode: generating learning_report.md ...")
        asyncio.run(_report_only())
    else:
        if args.rounds < 1:
            parser.error("--rounds must be >= 1")
        asyncio.run(_run(rounds=args.rounds))


if __name__ == "__main__":
    main()
