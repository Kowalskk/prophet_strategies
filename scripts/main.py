"""
PROPHET STRATEGIES — Main CLI

Commands:
    python main.py fetch                           # Download data from Dune
    python main.py backtest --strategy stink_bid   # Single backtest run
    python main.py export                          # CSV + XLSX output
    python main.py report                          # HTML dashboard
    python main.py all                             # export + report
"""
from __future__ import annotations
import logging
import os
import sys
from pathlib import Path

import click
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
load_dotenv()


def _setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def _load_cfg(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


@click.group()
@click.option("--config", default="config/config.yaml", show_default=True)
@click.option("--log-level", default="INFO", show_default=True)
@click.pass_context
def cli(ctx, config, log_level):
    """PROPHET STRATEGIES — Polymarket Crypto Backtesting System"""
    _setup_logging(log_level)
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config
    ctx.obj["cfg"] = _load_cfg(config)


# ── fetch ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--validate-only", is_flag=True)
@click.pass_context
def fetch(ctx, validate_only):
    """Download data from Dune Analytics and build local SQLite cache."""
    from datetime import date
    from data.data_manager import DataManager

    cfg      = ctx.obj["cfg"]
    data_cfg = cfg["data"]
    dm       = DataManager(db_path=data_cfg["cache_db"])

    if not validate_only:
        start = data_cfg["start_date"]
        end   = data_cfg.get("end_date") or date.today().isoformat()

        click.echo(f"Fetching trades {start} to {end} ...")
        click.echo(f"  {dm.fetch_trades(start, end):,} trade rows downloaded")
        click.echo("Fetching resolutions ...")
        click.echo(f"  {dm.fetch_resolutions(start, end):,} resolution rows downloaded")
        click.echo("Building markets ...")
        click.echo(f"  {dm.build_markets():,} markets built")
        click.echo("Fetching crypto prices ...")
        dm.fetch_prices(data_cfg["cryptos"], start, end)

    stats = dm.validate()
    click.echo("\n── Validation ──────────────────────────────────────")
    for k, v in stats.items():
        click.echo(f"  {k:<35s}: {v}")
    parse_r   = stats.get("parsed_markets", 0) / max(stats.get("total_markets", 1), 1) * 100
    resolve_r = stats.get("resolved_markets", 0) / max(stats.get("total_markets", 1), 1) * 100
    click.echo(f"  {'parse_rate':<35s}: {parse_r:.1f}%")
    click.echo(f"  {'resolve_rate':<35s}: {resolve_r:.1f}%")

    if stats.get("total_trades", 0) > 0:
        click.secho("\n✅  Data OK", fg="green", bold=True)
    else:
        click.secho("\n❌  No data — check DUNE_API_KEY and run again", fg="red")
    dm.close()


# ── backtest ──────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--strategy",       required=True,
              type=click.Choice(["stink_bid", "volatility_spread"]))
@click.option("--crypto",         default=None,
              type=click.Choice(["BTC", "ETH", "SOL", "ALL"]))
@click.option("--fill-model",     default="realistic",
              type=click.Choice(["optimistic", "realistic"]))
@click.option("--tier1-price",    default=0.03,  type=float)
@click.option("--tier1-capital",  default=50.0,  type=float)
@click.option("--tier2-price",    default=0.005, type=float)
@click.option("--tier2-capital",  default=3.0,   type=float)
@click.option("--exit",           "exit_strategy", default="hold_to_resolution")
@click.option("--spread-pct",     default=5.0,   type=float)
@click.option("--entry-max",      "entry_price_max", default=0.05, type=float)
@click.option("--capital-side",   default=100.0, type=float)
@click.option("--sell-target-pct",default=100.0, type=float)
@click.pass_context
def backtest(ctx, strategy, crypto, fill_model,
             tier1_price, tier1_capital, tier2_price, tier2_capital,
             exit_strategy, spread_pct, entry_price_max, capital_side, sell_target_pct):
    """Run a single backtest configuration and print results."""
    from data.data_manager import DataManager
    from backtest.engine import BacktestEngine

    cfg    = ctx.obj["cfg"]
    crypto = None if crypto in (None, "ALL") else crypto
    dm     = DataManager(db_path=cfg["data"]["cache_db"])
    engine = BacktestEngine(dm, fill_model=fill_model)

    if strategy == "stink_bid":
        params = {"tier1_price": tier1_price, "tier1_capital": tier1_capital,
                  "tier2_price": tier2_price, "tier2_capital": tier2_capital,
                  "exit_strategy": exit_strategy}
    else:
        params = {"spread_percent": spread_pct, "entry_price_max": entry_price_max,
                  "capital_per_side": capital_side, "exit_strategy": exit_strategy,
                  "sell_target_pct": sell_target_pct}

    r = engine.run(strategy_name=strategy, params=params, crypto=crypto)
    dm.close()

    click.echo("\n── Backtest Result ──────────────────────────────────")
    click.echo(f"  Strategy       : {r.strategy}")
    click.echo(f"  Crypto         : {r.crypto}")
    click.echo(f"  Fill model     : {r.fill_model}")
    click.echo(f"  Total trades   : {r.total_trades:,}")
    click.echo(f"  Filled         : {r.filled_trades:,}  ({r.fill_rate:.1%})")
    click.echo(f"  Win rate       : {r.win_rate:.1%}")
    click.echo(f"  Net P&L        : ${r.total_net_pnl:,.2f}")
    click.echo(f"  ROI            : {r.roi_pct:.1f}%")
    click.echo(f"  Sharpe ratio   : {r.sharpe_ratio:.4f}")
    click.echo(f"  Profit factor  : {r.profit_factor:.4f}")
    click.echo(f"  Max drawdown   : ${r.max_drawdown:,.2f}")


# ── export ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--input",  "input_csv", default="output/csv/master_results.csv",
              show_default=True)
@click.option("--output-dir", default="output", show_default=True)
@click.pass_context
def export(ctx, input_csv, output_dir):
    """Generate CSV top-results and XLSX analysis workbook."""
    if not Path(input_csv).exists():
        click.secho(f"File not found: {input_csv}", fg="red"); raise SystemExit(1)

    from analysis.export import ExportManager
    paths = ExportManager(output_dir=output_dir).export_all(input_csv)
    click.secho("\n✅  Export complete:", fg="green", bold=True)
    for k, p in paths.items():
        click.echo(f"  {k:<12s}: {p}")


# ── report ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--input", "input_csv", default="output/csv/master_results.csv",
              show_default=True)
@click.option("--output-dir", default="output", show_default=True)
@click.pass_context
def report(ctx, input_csv, output_dir):
    """Generate self-contained HTML dashboard."""
    if not Path(input_csv).exists():
        click.secho(f"File not found: {input_csv}", fg="red"); raise SystemExit(1)

    from analysis.report import ReportGenerator
    path = ReportGenerator(output_dir=output_dir).generate(input_csv)
    click.secho(f"\n✅  Dashboard: {path}", fg="green", bold=True)


# ── all ───────────────────────────────────────────────────────────────────────

@cli.command("all")
@click.option("--input", "input_csv", default="output/csv/master_results.csv",
              show_default=True)
@click.option("--output-dir", default="output", show_default=True)
@click.pass_context
def all_outputs(ctx, input_csv, output_dir):
    """Run export + report in one shot."""
    ctx.invoke(export, input_csv=input_csv, output_dir=output_dir)
    ctx.invoke(report, input_csv=input_csv, output_dir=output_dir)


if __name__ == "__main__":
    cli()
