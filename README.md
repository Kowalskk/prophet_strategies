# Polymarket Strategies

AI-powered trading bot for Polymarket with backtesting, position tracking, and real-time portfolio management.

## Project Structure

```
polymarket-strategies/
├── README.md                          # This file
├── .env                               # Environment variables (local)
├── docker-compose.yml                 # Docker services
├── vercel.json                        # Vercel deployment config
│
├── docs/                              # 📚 Documentation
│   ├── DEPLOY_VPS.md                 # VPS deployment guide
│   ├── IMPLEMENTATION_PLAN.md         # Technical implementation details
│   ├── PROJECT_CONTEXT.md            # Project overview & goals
│   ├── promp.txt                     # System prompts
│   ├── .env.example                  # Environment template
│   ├── README_OLD.md                 # Legacy README
│   ├── legacy/                       # Old documentation
│   └── .firecrawl/                   # Web scraping cache
│
├── src/                               # 💻 Source Code
│   ├── engine/                        # Backend (FastAPI/Python)
│   │   ├── app.py
│   │   ├── models/
│   │   ├── api/
│   │   └── services/
│   │
│   ├── dashboard/                     # Frontend (Next.js)
│   │   ├── app/
│   │   ├── components/
│   │   ├── lib/
│   │   ├── public/
│   │   └── package.json
│   │
│   ├── analysis/                      # Data analysis & metrics
│   │   ├── export.py
│   │   ├── metrics.py
│   │   ├── optimizer.py
│   │   └── report.py
│   │
│   ├── backtest/                      # Backtesting engine
│   │   ├── engine.py
│   │   ├── strategy_base.py
│   │   ├── strategies/
│   │   └── utils/
│   │
│   ├── models/                        # Data models & schemas
│   │   └── *.py
│   │
│   ├── config/                        # Configuration files
│   │   └── *.py
│   │
│   └── templates/                     # HTML templates
│
├── scripts/                           # 🔧 Utility Scripts
│   ├── main.py                        # Main entry point
│   ├── run_vps.py                     # VPS runner
│   ├── update_vps.py                  # VPS updater
│   │
│   ├── checks/                        # Data validation
│   │   ├── check_outcomes.py
│   │   ├── check_payouts.py
│   │   ├── check_progress.py
│   │   ├── check_readiness.py
│   │   ├── check_results.py
│   │   └── check_stats.py
│   │
│   ├── debug/                         # Debugging utilities
│   │   ├── debug_400.py
│   │   ├── debug_join.py
│   │   └── debug_resume.py
│   │
│   └── data/                          # Data operations
│       ├── analyze_progress.py
│       ├── clean_db.py
│       ├── final_check.py
│       ├── fix_resolutions.py
│       ├── rebuild_markets.py
│       ├── summarize_results.py
│       └── test_ids.py
│
├── data/                              # 📊 Data & Outputs
│   ├── output/                        # Results & reports
│   └── *.db                           # Database files
│
├── .claude/                           # Claude Code configuration
├── venv/                              # Python virtual environment
└── .git/                              # Git repository
```

## Quick Start

### Local Development

```bash
# 1. Setup Python environment
python -m venv venv
source venv/bin/activate  # or 'venv\Scripts\activate' on Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp docs/.env.example .env
# Edit .env with your credentials

# 4. Run engine
cd src/engine
python -m uvicorn app:app --reload

# 5. Run dashboard (separate terminal)
cd src/dashboard
npm install
npm run dev
```

### Docker

```bash
docker-compose up -d
```

## Key Components

- **Engine** (`src/engine/`): FastAPI backend handling market data, positions, and strategies
- **Dashboard** (`src/dashboard/`): Next.js frontend for portfolio tracking and analysis
- **Backtest** (`src/backtest/`): Strategy backtesting engine with grid search optimization
- **Analysis** (`src/analysis/`): Performance metrics and optimization tools

## Documentation

- [Deployment Guide](docs/DEPLOY_VPS.md) - VPS setup & deployment
- [Implementation Plan](docs/IMPLEMENTATION_PLAN.md) - Technical architecture
- [Project Context](docs/PROJECT_CONTEXT.md) - Goals & overview

## Scripts

**Data Operations:**
```bash
python scripts/data/clean_db.py
python scripts/data/analyze_progress.py
```

**Validation:**
```bash
python scripts/checks/check_readiness.py
python scripts/checks/check_progress.py
```

**Debugging:**
```bash
python scripts/debug/debug_resume.py
```

## Development

- See `docs/` for detailed documentation
- Use `scripts/` for common operations
- Keep `src/` organized by component type
- Store outputs in `data/output/`

## Environment

Required `.env` variables (see `docs/.env.example`):
- `DUNE_API_KEY` - Dune Analytics API key
- `POLYMARKET_API_KEY` - Polymarket API credentials
- `DATABASE_URL` - PostgreSQL connection string

---

**Last Updated**: 2026-03-21
