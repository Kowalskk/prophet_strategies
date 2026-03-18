"""
PROPHET STRATEGIES
Grid Runner — executes the full parameter grid search in parallel

Uses multiprocessing.Pool to parallelize across CPU cores.
Saves results incrementally so progress is preserved if interrupted.
"""
from __future__ import annotations
import csv
import logging
import multiprocessing as mp
import os
import time
from pathlib import Path
from typing import Iterator

from tqdm import tqdm

from analysis.metrics import result_to_row
from analysis.optimizer import (
    generate_stink_bid_combos,
    generate_volatility_spread_combos,
    count_combos,
)

logger = logging.getLogger(__name__)

# Worker-level globals (set once per process)
_worker_dm = None
_worker_db_path = None


def _init_worker(db_path: str):
    """Initialise a DataManager in each worker process with preloaded trades."""
    global _worker_dm, _worker_db_path
    try:
        import sys, os
        # Make sure the project root is on the path
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        # Setup logging for the worker process
        import logging
        from pathlib import Path
        fmt = "%(asctime)s %(levelname)-8s %(name)s [PID:%(process)d] — %(message)s"
        handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler("output/prophet.log")]
        logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)
        
        from data.data_manager import DataManager
        
        logger.info(f"Worker initializing DataManager...")
        _worker_dm = DataManager(db_path=db_path)
        # We skip preload_trades() here to save memory during initialization.
        # It will be called on-demand in _run_one for the specific crypto.
        _worker_dm.preload_markets()
        _worker_db_path = db_path
        logger.info(f"Worker initialization complete.")
    except Exception as e:
        import traceback
        with open("worker_error.log", "a") as f:
            f.write(f"ERROR in worker {os.getpid()}: {str(e)}\n")
            f.write(traceback.format_exc() + "\n")
        logger.error(f"Worker init error: {e}")
        raise


def _run_one(job: dict) -> dict | None:
    """Worker function: run one backtest job and return the result row."""
    global _worker_dm
    try:
        from backtest.engine import BacktestEngine
        # Ensure trades for the current crypto are preloaded
        _worker_dm.preload_trades(crypto=job["crypto"])
        
        engine = BacktestEngine(_worker_dm, fill_model=job["fill_model"])
        result = engine.run(
            strategy_name=job["strategy"],
            params=job["params"],
            crypto=job["crypto"],
        )
        return result_to_row(result)
    except Exception as e:
        import traceback
        logger.error(f"Worker execution error: {e}\n{traceback.format_exc()}")
        return None


class GridRunner:
    """
    Runs the full parameter grid search.
    - Generates all (strategy, crypto, fill_model, params) combos
    - Executes them in parallel using multiprocessing
    - Saves results to CSV incrementally
    """

    def __init__(
        self,
        cfg: dict,
        db_path: str,
        output_dir: str = "output/csv",
        n_workers: int = 4,
        save_interval: int = 100,
    ):
        self.cfg = cfg
        self.db_path = db_path
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.n_workers = n_workers
        self.save_interval = save_interval
        self.output_path = self.output_dir / "master_results.csv"

    def _all_jobs(self, quick: bool = False) -> Iterator[dict]:
        """Yield all jobs. If quick=True, yield a small subset for testing."""
        stink_bid_enabled    = self.cfg["strategies"]["stink_bid"]["enabled"]
        vol_spread_enabled   = self.cfg["strategies"]["volatility_spread"]["enabled"]

        if stink_bid_enabled:
            gen = generate_stink_bid_combos(self.cfg)
            if quick:
                gen = _take(gen, 20)
            yield from gen

        if vol_spread_enabled:
            gen = generate_volatility_spread_combos(self.cfg)
            if quick:
                gen = _take(gen, 20)
            yield from gen

    def count(self) -> dict:
        return count_combos(self.cfg)

    def run(self, quick: bool = False, resume: bool = True) -> Path:
        """
        Execute the grid search.

        Args:
            quick   : Run a tiny subset (for smoke-testing)
            resume  : Skip combos already saved in master_results.csv

        Returns:
            Path to master_results.csv
        """
        counts = self.count()
        total = 40 if quick else counts["total"]
        logger.info(f"Grid search: {counts} | workers={self.n_workers} | quick={quick}")

        # Load already-done rows if resuming
        done_keys: set[str] = set()
        existing_rows: list[dict] = []
        if resume and self.output_path.exists():
            existing_rows = _load_csv(self.output_path)
            for row in existing_rows:
                done_keys.add(_row_key(row))
            logger.info(f"Resuming: {len(done_keys)} results already saved")

        jobs = list(self._all_jobs(quick=quick))
        pending = [j for j in jobs if _job_key(j) not in done_keys]
        
        # IMPORTANT: Sort pending jobs by crypto to minimize worker memory thrashing
        pending.sort(key=lambda x: x["crypto"])
        
        logger.info(f"Pending: {len(pending)} jobs")

        if not pending:
            logger.info("All jobs already done.")
            return self.output_path

        results_buffer: list[dict] = list(existing_rows)
        csv_header: list[str] | None = None
        if existing_rows:
            csv_header = list(existing_rows[0].keys())

        start = time.time()
        completed = len(done_keys)

        ctx = mp.get_context("spawn")
        with ctx.Pool(
            processes=self.n_workers,
            initializer=_init_worker,
            initargs=(self.db_path,),
        ) as pool:
            with tqdm(total=len(pending), desc="Grid Search", unit="run") as pbar:
                for row in pool.imap_unordered(_run_one, pending, chunksize=4):
                    pbar.update(1)
                    completed += 1

                    if row is None:
                        continue

                    # Set CSV header from first non-None result
                    if csv_header is None:
                        csv_header = list(row.keys())

                    results_buffer.append(row)

                    # Periodic save
                    if len(results_buffer) % self.save_interval == 0:
                        _write_csv(self.output_path, results_buffer, csv_header)
                        elapsed = time.time() - start
                        rate = completed / max(elapsed, 1)
                        remaining = (len(pending) - completed) / max(rate, 0.001)
                        logger.info(
                            f"Saved {len(results_buffer)} rows | "
                            f"{rate:.1f} runs/s | ETA {remaining/60:.1f} min"
                        )

        # Final save
        if results_buffer and csv_header:
            _write_csv(self.output_path, results_buffer, csv_header)

        elapsed = time.time() - start
        logger.info(
            f"Grid search complete: {len(results_buffer)} results in {elapsed:.1f}s "
            f"→ {self.output_path}"
        )
        return self.output_path


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _format_val(v):
    try:
        f = float(v)
        if f.is_integer():
            return int(f)
        return round(f, 6)
    except (ValueError, TypeError):
        return str(v)

def _job_key(job: dict) -> str:
    p = job["params"]
    # Cast values to standardized strings/numbers to avoid type issues
    std_p = sorted([(str(k), _format_val(v)) for k, v in p.items()])
    return f"{job['strategy']}|{job['crypto']}|{job['fill_model']}|{std_p}"


def _row_key(row: dict) -> str:
    params = {k.replace("param_", ""): v for k, v in row.items() if k.startswith("param_")}
    std_p = sorted([(str(k), _format_val(v)) for k, v in params.items()])
    return f"{row.get('strategy')}|{row.get('crypto')}|{row.get('fill_model')}|{std_p}"


def _take(gen: Iterator, n: int) -> Iterator:
    for i, item in enumerate(gen):
        if i >= n:
            break
        yield item


def _write_csv(path: Path, rows: list[dict], header: list[str]):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _load_csv(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))
