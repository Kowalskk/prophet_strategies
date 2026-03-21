## Purpose & Context

Saulo is building **PROPHET STRATEGIES**, a Python-based automated trading system for Polymarket crypto prediction markets. The project has completed its backtesting phase (20,476 configurations tested) and is now transitioning to **Phase 2: Live Paper Trading System** with a dashboard deployed on Vercel and the trading engine on a VPS.

All conversations are conducted in informal Spanish.

## Architecture Overview

The system is split into two deployable units:

1. **Prophet Engine (VPS)** — Python backend that scans markets, generates signals, simulates/executes orders, and collects data. Exposes a REST API for the dashboard.
2. **Prophet Dashboard (Vercel)** — Next.js frontend that displays live positions, P&L, strategy performance, and allows market/strategy selection and configuration.

## Three Core Strategies

### 1. Volatility Spread (Best Risk-Adjusted)
- Places symmetric YES/NO limit orders at a spread from opening price
- Win rate: 40-60% with `sell_at_target` exit
- Best for: Weekly crypto markets with expected movement
- Key params: `spread_percent`, `entry_price_max`, `capital_per_side`, `sell_target_pct`

### 2. Stink Bid (Tail Risk Harvesting)
- Places ultra-cheap limit orders (1-5¢) on extreme YES/NO outcomes
- Win rate: 1-6%, but wins are 10x-150x multipliers
- Best for: Markets where implied vol is underpriced
- Key params: `tier1_price`, `tier2_price`, `tier1_capital`, `tier2_capital`, `exit_strategy`

### 3. Liquidity Sniper (New — To Be Developed)
- Monitors order book depth and places orders when liquidity gaps appear
- Targets mispriced markets where YES+NO prices deviate from $1.00
- Best for: Newly created or thinly traded markets
- Key params: `min_gap_pct`, `max_position_size`, `exit_timeout`

All strategies implement a common `StrategyBase` interface, making it trivial to add new strategies in the future.

## Current State (March 2026)

### Completed (Phase 1 — Backtesting)
- Full grid search completed: 20,476 configurations (20,188 stink_bid + 288 volatility_spread)
- SQLite database with 12.6M+ trades from Dune Analytics
- Two fill models (optimistic/realistic) validated
- Analysis exported to XLSX + HTML dashboard
- Key finding: 99.7% of configs profitable in backtest — likely inflated 3-5x vs reality due to fill assumptions and look-ahead bias

### In Progress (Phase 2 — Paper Trading + Dashboard)
- Building Prophet Engine: market scanner, signal generator, paper order simulator
- Building Prophet Dashboard: Next.js app on Vercel with live monitoring
- Integrating Polymarket CLOB API (py-clob-client) and Gamma API for live data
- Data collection pipeline to capture ALL market data for future analysis

### Architecture Decisions
- **VPS**: Hetzner or DigitalOcean (~$20/mo) for the Python engine
- **Dashboard**: Next.js on Vercel (free tier) consuming engine REST API
- **Database**: PostgreSQL on VPS (orders, fills, P&L, market snapshots)
- **Cache**: Redis on VPS (live prices, order book state, rate limiting)
- **API Reference**: Polymarket CLI (Rust, https://github.com/Polymarket/polymarket-cli) used as reference for API integration patterns

## Roadmap

### Phase 2A: Data Collection & Market Scanner (Current)
- [ ] Polymarket API integration (CLOB + Gamma) via py-clob-client
- [ ] Market scanner: detect new weekly crypto markets every Monday
- [ ] Order book snapshots: periodic captures for all target markets
- [ ] Price feed integration: real-time BTC/ETH/SOL spot prices
- [ ] PostgreSQL schema for live data storage
- [ ] REST API endpoints for dashboard consumption

### Phase 2B: Paper Trading Engine
- [ ] Strategy engine with pluggable strategy interface
- [ ] Paper order simulator (virtual fills based on live order book)
- [ ] Position tracker with real-time P&L
- [ ] Risk manager (position limits, drawdown stops, kill switch)
- [ ] Comprehensive logging and alerting

### Phase 2C: Dashboard (Vercel)
- [ ] Real-time positions and P&L display
- [ ] Strategy selector: pick which strategies run on which markets
- [ ] Configuration panel: adjust strategy parameters live
- [ ] Historical performance charts
- [ ] Risk metrics and alerts
- [ ] Market browser with order book visualization

### Phase 3: Live Trading (Future — requires 8+ weeks paper validation)
- [ ] Real order placement via CLOB API
- [ ] Wallet management (USDC on Polygon)
- [ ] Execution quality monitoring (actual vs simulated fills)
- [ ] Gradual capital scaling ($500 → $5K → $25K)

### Phase 4: Strategy Expansion (Future)
- [ ] Liquidity Sniper strategy implementation
- [ ] Volatility regime filters
- [ ] Cross-crypto correlation signals
- [ ] Event-driven timing (earnings, macro events)
- [ ] Custom strategy builder via dashboard

## Key Learnings & Principles

- **Dune free tier limitation**: Cannot create or execute queries via API; can only pull results from pre-saved queries.
- **Market filter discipline is critical**: Both SQL-level and application-level filtering needed to avoid corrupted backtest data.
- **Low win rate ≠ unprofitable**: 1-2% win rate with high multipliers is valid but requires verification that wins aren't concentrated in few events.
- **Backtest results are inflated**: Expect 3-5x degradation from backtest to live. Use realistic fill model as optimistic ceiling.
- **Don't automate execution prematurely**: Paper trade 8+ weeks before any real capital.
- **Polymarket API patterns**: Reference the Rust CLI for correct API usage — order signing, token approvals, CLOB auth flow.

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Engine | Python 3.11+, asyncio, aiohttp |
| Dashboard | Next.js 14, TypeScript, Tailwind, Recharts |
| Database | PostgreSQL 16 |
| Cache | Redis 7 |
| VPS | Hetzner/DigitalOcean (Ubuntu 22.04) |
| Frontend Hosting | Vercel (free tier) |
| Polymarket SDK | py-clob-client (Python) |
| Process Manager | systemd (VPS) |
| Monitoring | Built-in health endpoints + dashboard alerts |

## Risk Limits (Mandatory)

```python
MAX_POSITION_PER_MARKET = 100    # USD max per market
MAX_DAILY_LOSS = 200             # Stop if $200 daily loss
MAX_OPEN_POSITIONS = 20          # Max 20 active orders
MAX_CONCENTRATION = 0.25         # Max 25% capital in one crypto
MAX_DRAWDOWN_TOTAL = 0.30        # Stop at 30% total drawdown
KILL_SWITCH = True               # Manual panic button
```
