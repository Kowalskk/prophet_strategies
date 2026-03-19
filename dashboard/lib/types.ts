// TypeScript interfaces matching engine/prophet/api/schemas.py exactly

export interface SidePrice {
  bid: number | null;
  ask: number | null;
  ts: string | null;
}

/** market_id (as string key) → { yes: SidePrice, no: SidePrice } */
export type MarketPrices = Record<string, { yes?: SidePrice; no?: SidePrice }>;

export interface Health {
  status: string;
  version: string;
  uptime_seconds: number;
  paper_trading: boolean;
}

export interface SystemStatus {
  scanning_active: boolean;
  last_scan_at: string | null;
  open_positions: number;
  daily_pnl: number;
  kill_switch: boolean;
}

export interface Market {
  id: number;
  condition_id: string;
  question: string;
  crypto: string;
  threshold: number | null;
  direction: string | null;
  resolution_date: string | null;
  token_id_yes: string;
  token_id_no: string;
  status: string;
  resolved_outcome: string | null;
  resolution_time: string | null;
  created_at: string;
  updated_at: string;
}

export interface MarketList {
  items: Market[];
  total: number;
  limit: number;
  offset: number;
}

export interface OrderBookLevel {
  price: number;
  size: number;
}

export interface OrderBook {
  market_id: number;
  token_id: string;
  side: string;
  best_bid: number | null;
  best_ask: number | null;
  spread_pct: number | null;
  bid_depth_10pct: number;
  ask_depth_10pct: number;
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
  timestamp: string | null;
}

export interface Strategy {
  name: string;
  description: string;
  default_params: Record<string, unknown>;
  enabled: boolean;
}

export interface StrategyConfig {
  id: number;
  strategy: string;
  market_id: number | null;
  crypto: string | null;
  enabled: boolean;
  params: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface Position {
  id: number;
  market_id: number;
  strategy: string;
  side: string;
  entry_price: number;
  size_usd: number;
  shares: number;
  status: string;
  opened_at: string;
  closed_at: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  gross_pnl: number | null;
  fees: number | null;
  net_pnl: number | null;
  unrealized_pnl: number | null;
  current_price: number | null;
}

export interface PositionList {
  items: Position[];
  total: number;
}

export interface ClosedPositionList {
  items: Position[];
  total: number;
  limit: number;
  offset: number;
}

export interface PerformanceSummary {
  total_pnl: number;
  win_rate: number;
  sharpe_ratio: number;
  profit_factor: number;
  max_drawdown: number;
  total_trades: number;
  open_positions: number;
}

export interface PnLPoint {
  date: string; // YYYY-MM-DD
  pnl: number;
}

export interface StrategyBreakdown {
  name: string;
  net_pnl: number;
  trades: number;
  win_rate: number;
}

export interface Config {
  paper_trading: boolean;
  kill_switch: boolean;
  scan_interval_minutes: number;
  target_cryptos: string[];
  api_host: string;
  api_port: number;
  max_position_per_market: number;
  max_daily_loss: number;
  max_open_positions: number;
  max_concentration: number;
  max_drawdown_total: number;
}

export interface RiskMetrics {
  kill_switch: boolean;
  paper_trading: boolean;
  daily_loss_pct: number;
  open_positions_pct: number;
  drawdown_pct: number;
  raw: Record<string, unknown>;
}

export interface SpotPrice {
  crypto: string;
  price_usd: number;
  source: string;
  timestamp: string | null;
}

export interface SpotPrices {
  prices: SpotPrice[];
}

export interface SignalMarket {
  crypto: string;
  threshold: number | null;
  direction: string | null;
  resolution_date: string | null;
}

export interface Signal {
  id: number;
  market_id: number;
  strategy: string;
  side: string;
  target_price: number;
  size_usd: number;
  confidence: number;
  status: string;
  created_at: string;
  market: SignalMarket | null;
}

export interface SignalList {
  items: Signal[];
  total: number;
  limit: number;
  offset: number;
}

export interface SignalSummaryItem {
  strategy: string;
  status: string;
  count: number;
}
