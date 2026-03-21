"""
PROPHET STRATEGIES
VPS Runner — main script to execute the full grid search on a remote server.

Usage:
    python run_vps.py                  # Full grid search
    python run_vps.py --quick          # Smoke test (40 combos)
    python run_vps.py --no-resume      # Start fresh, ignore previous results
    python run_vps.py --workers 8      # Use 8 CPU cores
    python run_vps.py --strategy stink_bid  # Only run one strategy
"""
from __future__ import annotations
import logging
import os
import sys
from datetime import date
from pathlib import Path

import click
import yaml
from dotenv import load_dotenv

# ── ensure project root is on sys.path ──────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

load_dotenv()


def _setup_logging(log_level: str, log_file: str | None):
    fmt = "%(asctime)s %(levelname)-8s %(name)s — %(message)s"
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(level=getattr(logging, log_level.upper(), logging.INFO),
                        format=fmt, handlers=handlers)


@click.command()
@click.option("--config",    default="config/config.yaml", show_default=True)
@click.option("--quick",     is_flag=True, help="Smoke-test: 40 combos only")
@click.option("--no-resume", is_flag=True, help="Ignore previous results, start fresh")
@click.option("--workers",   default=None, type=int, help="Override parallel workers")
@click.option("--strategy",  default=None,
              type=click.Choice(["stink_bid", "volatility_spread", "both"]),
              help="Run only one strategy (default: both)")
@click.option("--crypto",    default=None,
              type=click.Choice(["BTC", "ETH", "SOL"]),
              help="Limit to a single crypto")
def main(config, quick, no_resume, workers, strategy, crypto):
    # ── Load config ──────────────────────────────────────────────────────────
    with open(config) as f:
        cfg = yaml.safe_load(f)

    exec_cfg   = cfg.get("execution", {})
    output_cfg = cfg.get("output", {})
    data_cfg   = cfg["data"]

    log_level = exec_cfg.get("log_level", "INFO")
    log_file  = exec_cfg.get("log_file", "output/prophet.log")
    _setup_logging(log_level, log_file)

    logger = logging.getLogger("prophet.vps")
    logger.info("=" * 60)
    logger.info("PROPHET STRATEGIES — Grid Search Runner")
    logger.info("=" * 60)

    # ── Override config with CLI flags ───────────────────────────────────────
    n_workers     = workers or exec_cfg.get("parallel_workers", 4)
    save_interval = exec_cfg.get("save_interval", 100)
    db_path       = data_cfg["cache_db"]
    output_dir    = output_cfg.get("dir", "output/")

    if strategy and strategy != "both":
        cfg["strategies"]["stink_bid"]["enabled"]          = (strategy == "stink_bid")
        cfg["strategies"]["volatility_spread"]["enabled"]  = (strategy == "volatility_spread")

    if crypto:
        cfg["data"]["cryptos"] = [crypto]

    # ── Validate data exists ─────────────────────────────────────────────────
    db = Path(db_path)
    if not db.exists():
        logger.error(f"Database not found: {db_path}")
        logger.error("Run first:  python -m data.data_manager --fetch --validate")
        sys.exit(1)

    from data.data_manager import DataManager
    dm = DataManager(db_path=db_path)
    stats = dm.validate()
    dm.close()

    logger.info(f"DB: {stats['total_trades']:,} trades | {stats['total_markets']:,} markets | "
                f"{stats['resolved_markets']:,} resolved")

    if stats["total_trades"] == 0:
        logger.error("No trade data found. Run --fetch first.")
        sys.exit(1)

    # ── Count combos ─────────────────────────────────────────────────────────
    from analysis.optimizer import count_combos
    counts = count_combos(cfg)
    logger.info(f"Grid combos: stink_bid={counts['stink_bid']:,} | "
                f"vol_spread={counts['volatility_spread']:,} | "
                f"total={counts['total']:,}")

    if quick:
        logger.info("QUICK MODE: running 40 combos only")

    # ── Run grid search ───────────────────────────────────────────────────────
    from backtest.grid_runner import GridRunner

    runner = GridRunner(
        cfg=cfg,
        db_path=db_path,
        output_dir=os.path.join(output_dir, "csv"),
        n_workers=n_workers,
        save_interval=save_interval,
    )

    results_path = runner.run(quick=quick, resume=not no_resume)

    logger.info(f"Results saved → {results_path}")

    # ── Rank and summarise ────────────────────────────────────────────────────
    if results_path.exists():
        import pandas as pd
        from analysis.optimizer import rank_results, best_per_strategy

        df = pd.read_csv(results_path)
        logger.info(f"Total results: {len(df):,} rows")

        # Convert numeric columns
        num_cols = ["total_net_pnl", "sharpe_ratio", "win_rate",
                    "profit_factor", "fill_rate", "filled_trades", "roi_pct", "max_drawdown"]
        for col in num_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        top = rank_results(df, top_n=cfg["output"].get("top_n_results", 50))
        if not top.empty:
            logger.info(f"\nTOP 10 configurations:")
            display_cols = ["strategy", "crypto", "fill_model", "total_net_pnl",
                            "sharpe_ratio", "win_rate", "profit_factor",
                            "roi_pct", "max_drawdown", "composite_score"]
            display_cols = [c for c in display_cols if c in top.columns]
            logger.info("\n" + top[display_cols].head(10).to_string(index=False))

            best = best_per_strategy(df)
            logger.info(f"\nBEST per (strategy × crypto × fill_model):")
            logger.info("\n" + best[display_cols].to_string(index=False))

            # Save top results separately
            top_path = Path(output_dir) / "csv" / "top_results.csv"
            top.to_csv(top_path, index=False)
            logger.info(f"Top {len(top)} results → {top_path}")

    logger.info("Done. Next step: run_vps.py → analysis/export.py → HTML report")


if __name__ == "__main__":
    main()
