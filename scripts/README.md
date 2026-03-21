# Scripts Directory

Utility scripts for data management, debugging, and validation.

## Organization

### `main.py` & `run_vps.py`
Entry points for running the application and grid search optimization.

### `checks/` - Data Validation
Validation and readiness checking scripts:
- `check_readiness.py` - Pre-flight checks
- `check_progress.py` - Monitor optimization progress
- `check_outcomes.py` - Verify market outcomes
- `check_payouts.py` - Validate payouts
- `check_results.py` - Analyze results
- `check_stats.py` - Generate statistics

### `debug/` - Debugging Utilities
Debugging scripts for troubleshooting:
- `debug_resume.py` - Resume interrupted processes
- `debug_join.py` - Fix join/merge issues
- `debug_400.py` - Diagnose API errors

### `data/` - Data Operations
Data manipulation and analysis:
- `clean_db.py` - Clean database
- `analyze_progress.py` - Analyze optimization progress
- `summarize_results.py` - Summarize results
- `final_check.py` - Final validation
- `fix_resolutions.py` - Fix market resolutions
- `rebuild_markets.py` - Rebuild market data
- `test_ids.py` - Test market IDs

## Usage

All scripts should be run from the project root:

```bash
python scripts/checks/check_readiness.py
python scripts/data/clean_db.py
python scripts/debug/debug_resume.py
```

Ensure you have:
1. Python virtual environment activated
2. `.env` file configured
3. Database available (local or PostgreSQL)
