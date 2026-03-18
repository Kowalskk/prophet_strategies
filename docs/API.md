# Prophet Strategies API Reference

The Prophet Engine provides a REST API and a WebSocket feed for the dashboard to consume.

## Base URL

```text
http://localhost:8000/api/v1
```

## Authentication

All endpoints (except WebSocket) require authentication via Bearer token using the `API_SECRET` defined in your environment. Add the following header to all requests:

```text
Authorization: Bearer <API_SECRET>
```

*(Note: If `API_SECRET` is left empty in local development, authentication is disabled).*

---

## 1. System Endpoints

### `GET /status`
Returns the current health and status of the Prophet engine.
- **Response**: `SystemStatus` (is_running, active_markets_count, open_positions_count, unread_logs, memory_mb, uptime_seconds, kill_switch_active, paper_trading)

### `POST /control/start`
Starts the trading loop.

### `POST /control/stop`
Stops the trading loop.

### `POST /control/kill-switch`
Activates the kill switch, immediately halting all new order placement. Requires manual restart in the UI/config to resume.

---

## 2. Market Endpoints

### `GET /markets`
Lists tracked markets with optional filtering.
- **Query Params**:
  - `crypto` (string, optional): Filter by crypto symbol (e.g., "BTC", "ETH").
  - `status` (string, optional): Filter by status ("active", "resolved", "expired").
- **Response**: `MarketList`

### `GET /markets/{market_id}`
Retrieves details for a specific market.
- **Path Params**: `market_id` (numeric ID or string condition_id).
- **Response**: `Market`

### `GET /markets/{market_id}/orderbook`
Retrieves the latest order book snapshot for the specified market.
- **Response**: `OrderBook`

### `GET /markets/{market_id}/trades`
Retrieves recent trades observed on the Polymarket CLOB for this market.
- **Query Params**:
  - `limit` (int, default: 50)
- **Response**: `List[TradeInfo]`

---

## 3. Position Endpoints

### `GET /positions`
Lists all currently open positions.
- **Response**: `PositionList`

### `GET /positions/closed`
Lists historical closed positions with pagination.
- **Query Params**:
  - `limit` (int, default: 50)
  - `offset` (int, default: 0)
- **Response**: `ClosedPositionList`

### `POST /positions/{position_id}/close`
Force closes an open position at the current market price (paper or live).
- **Body**: `{ "reason": "manual" }` (optional)
- **Response**: `Position`

---

## 4. Performance & Analytics Endpoints

### `GET /performance/summary`
Retrieves high-level performance metrics.
- **Response**: `PerformanceSummary` (total_pnl, win_rate, total_trades, sharpe_ratio)

### `GET /performance/history`
Retrieves daily P&L data points for charting.
- **Query Params**:
  - `days` (int, default: 30)
- **Response**: `List[PnLPoint]`

### `GET /performance/by-strategy`
Retrieves performance metrics grouped by strategy.
- **Response**: `List[StrategyBreakdown]`

### `GET /performance/by-crypto`
Retrieves performance metrics grouped by asset (BTC, ETH, etc.).
- **Response**: `List[StrategyBreakdown]`

---

## 5. Strategy Configuration Endpoints

### `GET /strategies`
Lists all available trading strategies and their global configurations.
- **Response**: `List[StrategyConfigResponse]`

### `PUT /strategies/{strategy_name}`
Updates the global configuration parameters for a strategy.
- **Body**: `StrategyConfigUpdate`
- **Response**: `StrategyConfigResponse`

### `GET /markets/{market_id}/strategies`
Lists strategy overrides specific to a market.

### `PUT /markets/{market_id}/strategies/{strategy_name}`
Sets market-specific parameters for a strategy (overrides global defaults).

---

## 6. Real-Time WebSocket Feeds

The engine provides WebSocket endpoints for real-time dashboard updates without polling.

Base URL: `ws://localhost:8000/ws`

### `WS /ws/status`
Pushes `SystemStatus` updates every second.

### `WS /ws/prices`
Pushes live spot prices for tracked cryptocurrencies.
- **Payload**: `{ "BTC": 65000.50, "ETH": 3500.25, ... }`

### `WS /ws/logs`
Streams live engine logs (INFO level and above) directly to the dashboard terminal component.
