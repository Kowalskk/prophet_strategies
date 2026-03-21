# PROPHET STRATEGIES — Implementation Plan

> Complete plan for Sonnet to execute. Each phase has explicit file paths, code structure, and acceptance criteria.

## Reference Material
- **Polymarket CLI (Rust)**: https://github.com/Polymarket/polymarket-cli — Use as API reference for CLOB auth, order signing, market queries
- **py-clob-client**: `pip install py-clob-client` — Python SDK for Polymarket CLOB API
- **Existing codebase**: `C:\Users\torra\OneDrive\Documentos\Polymarket Strategies\` — Backtesting system to reuse models and strategy logic

---

## Project Structure (Target)

```
prophet/
├── engine/                          # VPS — Python backend
│   ├── pyproject.toml
│   ├── requirements.txt
│   ├── .env.example
│   ├── alembic/                     # DB migrations
│   │   └── versions/
│   ├── prophet/
│   │   ├── __init__.py
│   │   ├── main.py                  # Entry point: starts all services
│   │   ├── config.py                # Settings via pydantic-settings (.env)
│   │   │
│   │   ├── api/                     # REST API for dashboard
│   │   │   ├── __init__.py
│   │   │   ├── app.py               # FastAPI app
│   │   │   ├── routes/
│   │   │   │   ├── markets.py       # GET /markets, /markets/{id}
│   │   │   │   ├── positions.py     # GET /positions, POST /positions/close
│   │   │   │   ├── strategies.py    # GET/PUT /strategies, /strategies/{id}/toggle
│   │   │   │   ├── performance.py   # GET /performance, /performance/history
│   │   │   │   ├── config.py        # GET/PUT /config (risk limits, params)
│   │   │   │   ├── system.py        # GET /health, /status, POST /kill-switch
│   │   │   │   └── data.py          # GET /data/orderbook/{id}, /data/snapshots
│   │   │   ├── middleware.py        # CORS, auth token, rate limiting
│   │   │   └── schemas.py          # Pydantic response models
│   │   │
│   │   ├── core/                    # Business logic
│   │   │   ├── __init__.py
│   │   │   ├── scanner.py           # Market scanner (Monday detection + continuous)
│   │   │   ├── signal_generator.py  # Evaluates markets → generates trade signals
│   │   │   ├── order_manager.py     # Paper/live order lifecycle
│   │   │   ├── position_tracker.py  # Track open positions, P&L
│   │   │   ├── risk_manager.py      # Enforce all risk limits
│   │   │   ├── data_collector.py    # Captures ALL useful data continuously
│   │   │   └── scheduler.py        # APScheduler: periodic tasks
│   │   │
│   │   ├── strategies/              # Pluggable strategy interface
│   │   │   ├── __init__.py
│   │   │   ├── base.py              # Abstract StrategyBase
│   │   │   ├── volatility_spread.py # Strategy 1
│   │   │   ├── stink_bid.py         # Strategy 2
│   │   │   ├── liquidity_sniper.py  # Strategy 3
│   │   │   └── registry.py         # Strategy registry (name → class)
│   │   │
│   │   ├── polymarket/              # API integration layer
│   │   │   ├── __init__.py
│   │   │   ├── clob_client.py       # Wrapper around py-clob-client
│   │   │   ├── gamma_client.py      # Market metadata + discovery
│   │   │   ├── orderbook.py         # Order book fetching + snapshots
│   │   │   ├── price_feeds.py       # BTC/ETH/SOL spot prices (CoinGecko/Binance)
│   │   │   └── models.py           # API response models
│   │   │
│   │   └── db/                      # Database layer
│   │       ├── __init__.py
│   │       ├── database.py          # SQLAlchemy async engine + session
│   │       ├── models.py            # ORM models (markets, orders, fills, snapshots)
│   │       └── repositories.py     # Data access layer (queries)
│   │
│   ├── scripts/
│   │   ├── setup_db.py              # Initialize PostgreSQL schema
│   │   ├── migrate_backtest.py      # Import backtest results from SQLite
│   │   └── deploy.sh               # VPS setup script (systemd, nginx, etc.)
│   │
│   └── tests/
│       ├── test_scanner.py
│       ├── test_strategies.py
│       ├── test_risk_manager.py
│       └── test_order_manager.py
│
├── dashboard/                       # Vercel — Next.js frontend
│   ├── package.json
│   ├── next.config.js
│   ├── tailwind.config.js
│   ├── tsconfig.json
│   ├── .env.local.example           # NEXT_PUBLIC_API_URL
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx           # Root layout + providers
│   │   │   ├── page.tsx             # Dashboard home (overview)
│   │   │   ├── markets/
│   │   │   │   └── page.tsx         # Market browser + order books
│   │   │   ├── strategies/
│   │   │   │   └── page.tsx         # Strategy config + toggle
│   │   │   ├── positions/
│   │   │   │   └── page.tsx         # Active positions + P&L
│   │   │   ├── performance/
│   │   │   │   └── page.tsx         # Historical performance charts
│   │   │   └── settings/
│   │   │       └── page.tsx         # Risk limits + system config
│   │   ├── components/
│   │   │   ├── layout/
│   │   │   │   ├── Sidebar.tsx
│   │   │   │   ├── Header.tsx
│   │   │   │   └── StatusBar.tsx    # Connection status + kill switch
│   │   │   ├── charts/
│   │   │   │   ├── PnLChart.tsx     # Cumulative P&L over time
│   │   │   │   ├── DrawdownChart.tsx
│   │   │   │   ├── WinRateChart.tsx
│   │   │   │   └── OrderBookViz.tsx # Live order book depth
│   │   │   ├── markets/
│   │   │   │   ├── MarketCard.tsx
│   │   │   │   ├── MarketTable.tsx
│   │   │   │   └── StrategySelector.tsx  # Pick strategies per market
│   │   │   ├── positions/
│   │   │   │   ├── PositionTable.tsx
│   │   │   │   └── PositionCard.tsx
│   │   │   └── common/
│   │   │       ├── KillSwitch.tsx   # Big red button
│   │   │       ├── StatCard.tsx
│   │   │       └── Loading.tsx
│   │   ├── lib/
│   │   │   ├── api.ts               # Fetch wrapper for engine API
│   │   │   ├── types.ts             # TypeScript interfaces
│   │   │   └── utils.ts
│   │   └── hooks/
│   │       ├── useMarkets.ts        # SWR/React Query hooks
│   │       ├── usePositions.ts
│   │       ├── usePerformance.ts
│   │       └── useWebSocket.ts      # Real-time updates (optional)
│   └── public/
│       └── favicon.ico
│
└── docs/
    ├── API.md                       # Engine REST API documentation
    └── DEPLOYMENT.md                # VPS + Vercel deployment guide
```

---

## PHASE 2A: Engine Core + Data Collection

### Step 1: Project Scaffolding
**Files to create:** `engine/pyproject.toml`, `engine/requirements.txt`, `engine/.env.example`, `engine/prophet/__init__.py`, `engine/prophet/config.py`

**requirements.txt:**
```
fastapi==0.115.*
uvicorn[standard]==0.34.*
sqlalchemy[asyncio]==2.0.*
asyncpg==0.30.*
alembic==1.14.*
redis==5.2.*
pydantic-settings==2.7.*
py-clob-client==0.18.*
httpx==0.28.*
apscheduler==3.11.*
python-dotenv==1.1.*
```

**config.py** — Use pydantic-settings to load from .env:
```python
class Settings(BaseSettings):
    # Polymarket
    polymarket_api_key: str = ""
    polymarket_secret: str = ""
    polymarket_passphrase: str = ""
    private_key: str = ""  # Polygon wallet
    chain_id: int = 137

    # Database
    database_url: str = "postgresql+asyncpg://prophet:prophet@localhost/prophet"
    redis_url: str = "redis://localhost:6379/0"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_secret: str = ""  # Bearer token for dashboard auth
    cors_origins: list[str] = ["https://prophet-dashboard.vercel.app"]

    # Risk
    max_position_per_market: float = 100.0
    max_daily_loss: float = 200.0
    max_open_positions: int = 20
    max_concentration: float = 0.25
    max_drawdown_total: float = 0.30
    kill_switch: bool = False

    # Trading mode
    paper_trading: bool = True  # MUST be True until validated

    # Scanner
    scan_interval_minutes: int = 15
    target_cryptos: list[str] = ["BTC", "ETH", "SOL"]

    model_config = SettingsConfigDict(env_file=".env")
```

### Step 2: Database Models
**File:** `engine/prophet/db/models.py`

**Tables to create:**

```python
# 1. Markets — discovered Polymarket markets
class Market(Base):
    __tablename__ = "markets"
    id: Mapped[int]                    # PK
    condition_id: Mapped[str]          # Polymarket condition ID (unique)
    question: Mapped[str]              # Full question text
    crypto: Mapped[str]                # BTC/ETH/SOL
    threshold: Mapped[float | None]    # Price threshold
    direction: Mapped[str | None]      # ABOVE/BELOW
    resolution_date: Mapped[date | None]
    token_id_yes: Mapped[str]          # CLOB token ID for YES
    token_id_no: Mapped[str]           # CLOB token ID for NO
    status: Mapped[str]               # active/resolved/expired
    resolved_outcome: Mapped[str | None]  # YES/NO
    resolution_time: Mapped[datetime | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

# 2. OrderBook Snapshots — periodic captures
class OrderBookSnapshot(Base):
    __tablename__ = "orderbook_snapshots"
    id: Mapped[int]
    market_id: Mapped[int]             # FK → markets
    token_id: Mapped[str]              # YES or NO token
    side: Mapped[str]                  # "yes" / "no"
    timestamp: Mapped[datetime]
    best_bid: Mapped[float | None]
    best_ask: Mapped[float | None]
    bid_depth_10pct: Mapped[float]     # Total USD within 10% of best bid
    ask_depth_10pct: Mapped[float]
    spread_pct: Mapped[float | None]
    raw_book: Mapped[dict]             # Full JSON snapshot (JSONB)

# 3. Trades — observed on-chain trades
class ObservedTrade(Base):
    __tablename__ = "observed_trades"
    id: Mapped[int]
    market_id: Mapped[int]
    token_id: Mapped[str]
    side: Mapped[str]                  # YES/NO
    price: Mapped[float]
    size_usd: Mapped[float]
    timestamp: Mapped[datetime]
    maker: Mapped[str]
    taker: Mapped[str]

# 4. Signals — generated trade signals
class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[int]
    market_id: Mapped[int]
    strategy: Mapped[str]              # volatility_spread/stink_bid/liquidity_sniper
    side: Mapped[str]                  # YES/NO
    target_price: Mapped[float]
    size_usd: Mapped[float]
    confidence: Mapped[float]          # 0-1
    params: Mapped[dict]               # Strategy-specific params (JSONB)
    status: Mapped[str]                # pending/executed/expired/rejected
    created_at: Mapped[datetime]

# 5. Paper Orders — simulated order placement
class PaperOrder(Base):
    __tablename__ = "paper_orders"
    id: Mapped[int]
    signal_id: Mapped[int]             # FK → signals
    market_id: Mapped[int]
    strategy: Mapped[str]
    side: Mapped[str]                  # YES/NO
    order_type: Mapped[str]            # limit
    target_price: Mapped[float]
    size_usd: Mapped[float]
    status: Mapped[str]                # open/filled/partially_filled/cancelled/expired
    placed_at: Mapped[datetime]
    filled_at: Mapped[datetime | None]
    fill_price: Mapped[float | None]
    fill_size_usd: Mapped[float | None]
    cancel_reason: Mapped[str | None]

# 6. Positions — aggregated from filled orders
class Position(Base):
    __tablename__ = "positions"
    id: Mapped[int]
    market_id: Mapped[int]
    strategy: Mapped[str]
    side: Mapped[str]
    entry_price: Mapped[float]
    size_usd: Mapped[float]
    shares: Mapped[float]
    status: Mapped[str]                # open/closed
    opened_at: Mapped[datetime]
    closed_at: Mapped[datetime | None]
    exit_price: Mapped[float | None]
    exit_reason: Mapped[str | None]    # resolution/target_hit/stop_loss/manual
    gross_pnl: Mapped[float | None]
    fees: Mapped[float | None]
    net_pnl: Mapped[float | None]

# 7. Price Snapshots — spot prices for cryptos
class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    id: Mapped[int]
    crypto: Mapped[str]
    price_usd: Mapped[float]
    source: Mapped[str]                # coingecko/binance
    timestamp: Mapped[datetime]

# 8. System State — runtime config & state
class SystemState(Base):
    __tablename__ = "system_state"
    key: Mapped[str]                   # PK
    value: Mapped[dict]                # JSONB
    updated_at: Mapped[datetime]

# 9. Strategy Configs — persisted strategy params per market
class StrategyConfig(Base):
    __tablename__ = "strategy_configs"
    id: Mapped[int]
    strategy: Mapped[str]
    market_id: Mapped[int | None]      # NULL = default for all markets
    crypto: Mapped[str | None]         # NULL = all cryptos
    enabled: Mapped[bool]
    params: Mapped[dict]               # JSONB: strategy-specific parameters
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
```

### Step 3: Polymarket Integration Layer
**Files:** `engine/prophet/polymarket/clob_client.py`, `gamma_client.py`, `orderbook.py`, `price_feeds.py`, `models.py`

**clob_client.py** — Thin wrapper around py-clob-client:
```python
class PolymarketClient:
    """
    Wraps py-clob-client. Reference the Polymarket CLI's clob module for patterns:
    https://github.com/Polymarket/polymarket-cli

    Key operations:
    - get_markets() → list of active markets with token IDs
    - get_orderbook(token_id) → bids/asks with depth
    - get_price(token_id) → current best bid/ask/mid
    - get_trades(token_id, since) → recent trades
    - place_limit_order(token_id, side, price, size) → order ID (LIVE ONLY)
    - cancel_order(order_id) → bool
    - get_open_orders() → list of user's open orders

    For paper trading: only use read operations (markets, orderbook, price, trades)
    Order placement is simulated by order_manager.py against live orderbook data.
    """
```

**gamma_client.py** — Market discovery:
```python
class GammaClient:
    """
    Queries Polymarket Gamma API for:
    - Market metadata (question, slug, conditions, tokens)
    - Event grouping
    - Market search by keyword
    - Resolution status

    Endpoint: https://gamma-api.polymarket.com
    No auth required for read operations.

    Key methods:
    - search_markets(query="BTC weekly") → filter crypto price markets
    - get_market(condition_id) → full market details
    - get_events(tag="crypto") → grouped events
    """
```

**orderbook.py** — Snapshot logic:
```python
class OrderBookService:
    """
    Periodically fetches and stores order book snapshots.
    Runs every scan_interval_minutes.

    For each active market:
    1. Fetch YES and NO order books from CLOB
    2. Calculate: best_bid, best_ask, spread, depth at various levels
    3. Store snapshot in PostgreSQL
    4. Cache current state in Redis (for fast dashboard reads)

    Data captured per snapshot:
    - Full bid/ask arrays (stored as JSONB)
    - Computed metrics: spread, depth, imbalance
    - Timestamp

    This data is CRITICAL for:
    - Paper fill simulation (did price cross our limit?)
    - Liquidity Sniper strategy (detect gaps)
    - Post-hoc analysis (was the order book deep enough to fill?)
    """
```

**price_feeds.py:**
```python
class PriceFeedService:
    """
    Fetches BTC/ETH/SOL spot prices every 1 minute.
    Sources: CoinGecko (free) or Binance WebSocket (real-time).

    Stores in PostgreSQL (1-min granularity) and Redis (latest price).
    Used by:
    - Volatility Spread strategy (calculate spread from current price)
    - Risk manager (portfolio value in USD)
    - Dashboard display
    """
```

### Step 4: Market Scanner
**File:** `engine/prophet/core/scanner.py`

```python
class MarketScanner:
    """
    Detects new Polymarket weekly crypto price markets.

    Schedule:
    - Full scan: Every Monday 00:00 UTC (new weekly markets appear)
    - Quick scan: Every 15 minutes (catch stragglers, update status)

    Logic:
    1. Query Gamma API for markets matching crypto price patterns
    2. Parse question text (reuse market_resolver.py patterns from backtest)
    3. Filter: only BTC/ETH/SOL, only ABOVE/BELOW, only weekly
    4. For new markets: fetch token IDs, store in DB, start tracking
    5. For resolved markets: update outcome, close positions

    Must capture for each market:
    - condition_id, question, token_id_yes, token_id_no
    - Parsed: crypto, threshold, direction, resolution_date
    - Status tracking: active → resolved
    """
```

### Step 5: Data Collector
**File:** `engine/prophet/core/data_collector.py`

```python
class DataCollector:
    """
    Captures ALL data that could be useful for future analysis.
    Runs continuously on scheduled intervals.

    Data streams (with intervals):
    ┌─────────────────────────┬────────────┬─────────────────────────┐
    │ Data                    │ Interval   │ Storage                 │
    ├─────────────────────────┼────────────┼─────────────────────────┤
    │ Order book snapshots    │ 5 min      │ PostgreSQL + Redis      │
    │ Spot prices (BTC/ETH/SOL)│ 1 min     │ PostgreSQL + Redis      │
    │ Recent trades (CLOB)    │ 2 min      │ PostgreSQL              │
    │ Market metadata         │ 15 min     │ PostgreSQL              │
    │ Market resolution check │ 5 min      │ PostgreSQL              │
    │ System metrics          │ 1 min      │ Redis (ephemeral)       │
    └─────────────────────────┴────────────┴─────────────────────────┘

    Critical principle: CAPTURE EVERYTHING NOW, ANALYZE LATER.
    Storage is cheap. Missing data is irreplaceable.

    Data retention:
    - Order book snapshots: raw JSONB kept for 90 days, aggregated forever
    - Trades: kept forever
    - Prices: 1-min kept 90 days, 1-hour aggregated forever
    """
```

### Step 6: REST API
**File:** `engine/prophet/api/app.py` + routes

**Endpoints:**

```
# System
GET    /health                    → { status, uptime, mode }
GET    /status                    → { scanning, trading, positions_count, daily_pnl }
POST   /kill-switch               → Toggle kill switch

# Markets
GET    /markets                   → List active markets (with filters)
GET    /markets/{id}              → Market detail + current orderbook
GET    /markets/{id}/orderbook    → Live order book data
GET    /markets/{id}/trades       → Recent trades for market

# Strategies
GET    /strategies                → List all strategies with enabled/disabled status
PUT    /strategies/{name}/toggle  → Enable/disable a strategy
GET    /strategies/{name}/config  → Get strategy params
PUT    /strategies/{name}/config  → Update strategy params
POST   /strategies/{name}/assign  → Assign strategy to specific market(s)

# Positions
GET    /positions                 → Active positions with live P&L
GET    /positions/closed          → Closed positions history
POST   /positions/{id}/close      → Manually close a position

# Performance
GET    /performance/summary       → Overall stats (total P&L, Sharpe, win rate)
GET    /performance/history       → Time series P&L data for charts
GET    /performance/by-strategy   → Breakdown by strategy
GET    /performance/by-crypto     → Breakdown by crypto

# Config
GET    /config                    → Current risk limits and settings
PUT    /config                    → Update risk limits
GET    /config/risk               → Risk metrics (current exposure, drawdown)

# Data
GET    /data/prices               → Latest crypto prices
GET    /data/snapshots/{market_id}→ Order book snapshot history
```

**Auth:** Simple Bearer token in `Authorization` header. Token set in .env.

**CORS:** Allow only the Vercel dashboard domain.

---

## PHASE 2B: Strategy Engine + Paper Trading

### Step 7: Strategy Interface
**File:** `engine/prophet/strategies/base.py`

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class TradeSignal:
    market_id: int
    side: str              # "YES" or "NO"
    target_price: float
    size_usd: float
    confidence: float      # 0.0-1.0
    exit_strategy: str     # "hold_to_resolution", "sell_at_target", etc.
    exit_params: dict      # {"target_pct": 100} etc.
    metadata: dict         # Strategy-specific data

class StrategyBase(ABC):
    name: str
    description: str
    default_params: dict

    @abstractmethod
    async def evaluate(self, market: Market, orderbook: dict, spot_price: float, params: dict) -> list[TradeSignal]:
        """
        Given a market and current data, return trade signals (0 or more).
        Called on every scan cycle for each active market assigned to this strategy.
        """
        ...

    @abstractmethod
    def validate_params(self, params: dict) -> dict:
        """Validate and normalize strategy parameters."""
        ...
```

### Step 8: Three Strategy Implementations

**volatility_spread.py:**
```python
class VolatilitySpreadStrategy(StrategyBase):
    name = "volatility_spread"
    description = "Symmetric YES/NO orders capturing bidirectional volatility"
    default_params = {
        "spread_percent": 5.0,       # % from mid price to place orders
        "entry_price_max": 0.05,     # Max price to pay (avoid overpaying)
        "capital_per_side": 50.0,    # USD per YES and NO order
        "exit_strategy": "sell_at_target",
        "sell_target_pct": 100.0,    # Exit at 2x
    }

    async def evaluate(self, market, orderbook, spot_price, params):
        # 1. Get current YES mid price from orderbook
        # 2. Calculate target YES price = mid - (mid * spread_percent / 100)
        # 3. Calculate target NO price = (1 - mid) - ((1-mid) * spread_percent / 100)
        # 4. Skip if either price > entry_price_max
        # 5. Return two signals: one YES, one NO
```

**stink_bid.py:**
```python
class StinkBidStrategy(StrategyBase):
    name = "stink_bid"
    description = "Ultra-cheap limit orders on extreme outcomes for high-multiplier payoffs"
    default_params = {
        "tier1_price": 0.03,         # 3¢
        "tier1_capital": 50.0,
        "tier2_price": 0.005,        # 0.5¢
        "tier2_capital": 3.0,
        "exit_strategy": "hold_to_resolution",
    }

    async def evaluate(self, market, orderbook, spot_price, params):
        # 1. Check if market has sufficient volume/depth
        # 2. Place 4 orders: tier1 YES, tier1 NO, tier2 YES, tier2 NO
        # 3. Skip tiers where current best ask < our target (already too cheap)
        # 4. Return up to 4 signals
```

**liquidity_sniper.py:**
```python
class LiquiditySniperStrategy(StrategyBase):
    name = "liquidity_sniper"
    description = "Exploits liquidity gaps and YES+NO mispricing"
    default_params = {
        "min_gap_pct": 3.0,          # Min gap between YES+NO implied prob and 100%
        "max_position_size": 100.0,
        "exit_timeout_hours": 24,    # Close if not profitable within 24h
        "min_book_depth": 50.0,      # Min USD depth to consider
    }

    async def evaluate(self, market, orderbook, spot_price, params):
        # 1. Fetch both YES and NO best asks
        # 2. Calculate combined cost: best_ask_yes + best_ask_no
        # 3. If combined < 1.0 - min_gap_pct/100: BOTH are cheap → buy both
        # 4. If one side has thin book (depth < min): place order at gap price
        # 5. Return signals for mispriced side(s)
```

**registry.py:**
```python
STRATEGY_REGISTRY: dict[str, type[StrategyBase]] = {
    "volatility_spread": VolatilitySpreadStrategy,
    "stink_bid": StinkBidStrategy,
    "liquidity_sniper": LiquiditySniperStrategy,
}

def get_strategy(name: str) -> StrategyBase:
    return STRATEGY_REGISTRY[name]()

def register_strategy(cls: type[StrategyBase]):
    """Decorator for adding new strategies."""
    STRATEGY_REGISTRY[cls.name] = cls
    return cls
```

### Step 9: Paper Order Manager
**File:** `engine/prophet/core/order_manager.py`

```python
class OrderManager:
    """
    Manages paper order lifecycle.

    Paper fill logic:
    1. When a signal is generated → create PaperOrder with status='open'
    2. On each scan cycle, for each open order:
       a. Fetch current orderbook
       b. Check if any observed trades crossed our target price
       c. If YES: simulate fill with slippage model
          - fill_price = target_price + (spread * slippage_factor)
          - fill_size = min(order_size, available_depth * queue_fraction)
       d. If order age > expiry_hours: cancel
    3. On fill → create Position, notify dashboard via API

    Paper fill model (MORE conservative than backtest realistic):
    - queue_multiplier: 5.0 (5x competition, up from 3x)
    - slippage_bps: 100 (1%, up from 0.5%)
    - min_volume: 25 USD (up from 10 USD)
    - Must see ACTUAL trade at target price or better (not just book presence)
    """
```

### Step 10: Risk Manager
**File:** `engine/prophet/core/risk_manager.py`

```python
class RiskManager:
    """
    Enforces all risk limits. Called BEFORE any order is placed.
    Returns (approved: bool, reason: str).

    Checks (in order):
    1. Kill switch is OFF
    2. Paper trading mode is ON (until explicitly switched)
    3. Daily loss < MAX_DAILY_LOSS
    4. Open positions < MAX_OPEN_POSITIONS
    5. Position in this market < MAX_POSITION_PER_MARKET
    6. Concentration in this crypto < MAX_CONCENTRATION
    7. Total drawdown from peak < MAX_DRAWDOWN_TOTAL

    All rejections are logged with reason.
    Dashboard shows current risk utilization (% of each limit used).
    """
```

---

## PHASE 2C: Dashboard (Vercel)

### Step 11: Next.js Scaffolding
```bash
npx create-next-app@latest dashboard --typescript --tailwind --app --eslint
cd dashboard
npm install recharts swr lucide-react @radix-ui/react-switch @radix-ui/react-dialog
```

### Step 12: Dashboard Pages

**Home (page.tsx)** — Overview:
- 4 stat cards: Total P&L, Win Rate, Active Positions, Daily P&L
- Mini P&L chart (last 7 days)
- Active positions table (top 5)
- System status indicator (scanning/idle/error/killed)
- Kill switch button (prominent, red)

**Markets (markets/page.tsx):**
- Table of all active crypto markets from Polymarket
- For each market: question, crypto, resolution date, current YES/NO prices
- Dropdown to assign strategies to each market (multi-select)
- Order book visualization (depth chart) on click
- Filter by crypto (BTC/ETH/SOL)

**Strategies (strategies/page.tsx):**
- Card per strategy with enable/disable toggle
- Expandable config panel for each strategy's parameters
- Slider/input for each param with defaults shown
- "Apply to all markets" vs "per-market config" toggle
- Strategy description and expected behavior

**Positions (positions/page.tsx):**
- Table: market, strategy, side, entry price, current price, unrealized P&L, age
- Color-coded: green for profit, red for loss
- Close button per position (manual exit)
- Tabs: Active | Closed | All
- Closed positions show exit reason and realized P&L

**Performance (performance/page.tsx):**
- Cumulative P&L chart (Recharts line chart)
- Drawdown chart
- Win rate over time
- Breakdown by strategy (bar chart)
- Breakdown by crypto (bar chart)
- Key metrics table: Sharpe, profit factor, max drawdown, avg trade duration

**Settings (settings/page.tsx):**
- Risk limits with live sliders
- System mode: Paper/Live toggle (with confirmation dialog)
- API connection status
- Scanner interval config
- Data retention settings

### Step 13: API Client
**File:** `dashboard/src/lib/api.ts`

```typescript
const API_URL = process.env.NEXT_PUBLIC_API_URL;
const API_TOKEN = process.env.NEXT_PUBLIC_API_TOKEN;

async function fetchAPI<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      'Authorization': `Bearer ${API_TOKEN}`,
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// Typed endpoints
export const api = {
  health: () => fetchAPI<Health>('/health'),
  status: () => fetchAPI<SystemStatus>('/status'),
  markets: () => fetchAPI<Market[]>('/markets'),
  positions: () => fetchAPI<Position[]>('/positions'),
  performance: () => fetchAPI<PerformanceSummary>('/performance/summary'),
  performanceHistory: () => fetchAPI<PnLPoint[]>('/performance/history'),
  strategies: () => fetchAPI<Strategy[]>('/strategies'),
  toggleStrategy: (name: string) => fetchAPI<void>(`/strategies/${name}/toggle`, { method: 'PUT' }),
  updateConfig: (config: Partial<Config>) => fetchAPI<void>('/config', { method: 'PUT', body: JSON.stringify(config) }),
  killSwitch: () => fetchAPI<void>('/kill-switch', { method: 'POST' }),
};
```

---

## PHASE 2D: Deployment

### Step 14: VPS Setup Script
**File:** `engine/scripts/deploy.sh`

```bash
#!/bin/bash
# Run on fresh Ubuntu 22.04 VPS

# Install dependencies
sudo apt update && sudo apt install -y python3.11 python3.11-venv postgresql redis-server nginx certbot

# Create app user
sudo useradd -m -s /bin/bash prophet

# Setup PostgreSQL
sudo -u postgres createuser prophet
sudo -u postgres createdb prophet -O prophet

# Setup Python environment
sudo -u prophet python3.11 -m venv /home/prophet/venv
sudo -u prophet /home/prophet/venv/bin/pip install -r requirements.txt

# Systemd service
sudo tee /etc/systemd/system/prophet.service << 'EOF'
[Unit]
Description=Prophet Trading Engine
After=postgresql.service redis.service

[Service]
Type=simple
User=prophet
WorkingDirectory=/home/prophet/engine
Environment=PATH=/home/prophet/venv/bin
ExecStart=/home/prophet/venv/bin/python -m prophet.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable prophet
sudo systemctl start prophet

# Nginx reverse proxy (for HTTPS → FastAPI)
# Configure with certbot for SSL
```

### Step 15: Vercel Deployment
```bash
cd dashboard
vercel --prod
# Set env vars in Vercel dashboard:
# NEXT_PUBLIC_API_URL=https://your-vps-domain.com/api
# NEXT_PUBLIC_API_TOKEN=your-secret-token
```

---

## Acceptance Criteria (Per Phase)

### Phase 2A ✓ when:
- [ ] `python -m prophet.main` starts FastAPI server
- [ ] Scanner detects current week's BTC/ETH/SOL markets from Polymarket
- [ ] Order book snapshots are being stored every 5 minutes
- [ ] Spot prices updating every 1 minute
- [ ] `/health` and `/markets` endpoints return valid data
- [ ] PostgreSQL has tables created and receiving data

### Phase 2B ✓ when:
- [ ] All 3 strategies generate signals for active markets
- [ ] Paper orders are created and tracked
- [ ] Paper fills happen when observed trades cross target prices
- [ ] Positions are opened/closed with P&L calculation
- [ ] Risk manager blocks orders exceeding limits
- [ ] `/positions` and `/performance/summary` return real paper trading data

### Phase 2C ✓ when:
- [ ] Dashboard deployed on Vercel, connects to VPS API
- [ ] All 6 pages render with live data
- [ ] Can toggle strategies on/off from dashboard
- [ ] Can assign strategies to specific markets
- [ ] Can adjust strategy parameters and risk limits
- [ ] Kill switch works (stops all trading within 1 scan cycle)
- [ ] P&L chart shows historical performance

### Phase 2D ✓ when:
- [ ] Engine runs as systemd service on VPS
- [ ] Survives VPS reboot (auto-restart)
- [ ] HTTPS enabled via nginx + certbot
- [ ] Dashboard loads in <3 seconds
- [ ] Data persists across restarts

---

## Implementation Order for Sonnet

Execute in this exact order, completing each file fully before moving to the next:

```
BATCH 1 — Scaffolding:
  1. engine/pyproject.toml + requirements.txt
  2. engine/.env.example
  3. engine/prophet/config.py
  4. engine/prophet/db/database.py
  5. engine/prophet/db/models.py

BATCH 2 — Polymarket Integration:
  6. engine/prophet/polymarket/models.py
  7. engine/prophet/polymarket/clob_client.py
  8. engine/prophet/polymarket/gamma_client.py
  9. engine/prophet/polymarket/orderbook.py
  10. engine/prophet/polymarket/price_feeds.py

BATCH 3 — Core Logic:
  11. engine/prophet/core/scanner.py
  12. engine/prophet/core/data_collector.py
  13. engine/prophet/strategies/base.py
  14. engine/prophet/strategies/volatility_spread.py
  15. engine/prophet/strategies/stink_bid.py
  16. engine/prophet/strategies/liquidity_sniper.py
  17. engine/prophet/strategies/registry.py
  18. engine/prophet/core/signal_generator.py
  19. engine/prophet/core/order_manager.py
  20. engine/prophet/core/position_tracker.py
  21. engine/prophet/core/risk_manager.py
  22. engine/prophet/core/scheduler.py

BATCH 4 — API:
  23. engine/prophet/api/schemas.py
  24. engine/prophet/api/middleware.py
  25. engine/prophet/api/routes/system.py
  26. engine/prophet/api/routes/markets.py
  27. engine/prophet/api/routes/strategies.py
  28. engine/prophet/api/routes/positions.py
  29. engine/prophet/api/routes/performance.py
  30. engine/prophet/api/routes/config.py
  31. engine/prophet/api/routes/data.py
  32. engine/prophet/api/app.py
  33. engine/prophet/main.py

BATCH 5 — Dashboard:
  34. dashboard scaffolding (create-next-app)
  35. dashboard/src/lib/types.ts
  36. dashboard/src/lib/api.ts
  37. dashboard/src/lib/utils.ts
  38. dashboard/src/components/common/*
  39. dashboard/src/components/layout/*
  40. dashboard/src/components/charts/*
  41. dashboard/src/components/markets/*
  42. dashboard/src/components/positions/*
  43. dashboard/src/hooks/*
  44. dashboard/src/app/layout.tsx
  45. dashboard/src/app/page.tsx (home)
  46. dashboard/src/app/markets/page.tsx
  47. dashboard/src/app/strategies/page.tsx
  48. dashboard/src/app/positions/page.tsx
  49. dashboard/src/app/performance/page.tsx
  50. dashboard/src/app/settings/page.tsx

BATCH 6 — Deployment:
  51. engine/scripts/deploy.sh
  52. engine/scripts/setup_db.py
  53. Alembic migrations
  54. docs/DEPLOYMENT.md
```

---

## Key Design Decisions for Sonnet

1. **NEVER place real orders** — `paper_trading=True` is enforced in config. The `clob_client.place_order()` method must check this flag and raise if False.

2. **Strategy assignment is per-market** — The `strategy_configs` table allows the user to pick which strategies run on which markets via the dashboard. A market can have 0, 1, or all 3 strategies assigned.

3. **New strategies are added by**: (a) creating a new file in `strategies/`, (b) subclassing `StrategyBase`, (c) decorating with `@register_strategy`. No other changes needed.

4. **Paper fills use OBSERVED TRADES, not order book presence** — A paper order fills ONLY when we observe an actual trade at or better than our target price. This is more conservative than the backtest realistic model.

5. **All data collection runs independently of trading** — Even if trading is paused (kill switch), data collection continues. Data is the most valuable output of this phase.

6. **Dashboard auth is a simple Bearer token** — Not user accounts. Single-operator system. Token in .env on both VPS and Vercel.

7. **Redis is for hot data only** — Latest prices, current orderbook, system state. PostgreSQL is source of truth for everything.
