import type {
  Health,
  SystemStatus,
  Market,
  MarketList,
  OrderBook,
  Strategy,
  StrategyConfig,
  Position,
  PositionList,
  ClosedPositionList,
  PerformanceSummary,
  PnLPoint,
  StrategyBreakdown,
  Config,
  RiskMetrics,
  SpotPrices,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";
const API_TOKEN = process.env.NEXT_PUBLIC_API_TOKEN ?? "";

async function fetchAPI<T>(path: string, options?: RequestInit): Promise<T | null> {
  try {
    const res = await fetch(`${API_URL}${path}`, {
      ...options,
      headers: {
        Authorization: `Bearer ${API_TOKEN}`,
        "Content-Type": "application/json",
        ...options?.headers,
      },
    });
    if (!res.ok) {
      console.error(`API error ${res.status} for ${path}`);
      return null;
    }
    return (await res.json()) as T;
  } catch (err) {
    console.error(`API fetch failed for ${path}:`, err);
    return null;
  }
}

// SWR-compatible fetcher (typed as returning unknown, cast at call site)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const fetcher = (path: string): Promise<any> => fetchAPI(path) as Promise<any>;

export const api = {
  // System
  health: () => fetchAPI<Health>("/health"),
  status: () => fetchAPI<SystemStatus>("/status"),
  killSwitch: () => fetchAPI<{ message: string }>("/kill-switch", { method: "POST" }),

  // Markets
  markets: (crypto?: string) =>
    fetchAPI<MarketList>(`/markets${crypto && crypto !== "All" ? `?crypto=${crypto}` : ""}`),
  market: (id: number) => fetchAPI<Market>(`/markets/${id}`),
  marketOrderBook: (id: number) => fetchAPI<OrderBook>(`/markets/${id}/orderbook`),

  // Strategies
  strategies: () => fetchAPI<Strategy[]>("/strategies"),
  toggleStrategy: (name: string) =>
    fetchAPI<{ message: string }>(`/strategies/${name}/toggle`, { method: "PUT" }),
  strategyConfig: (name: string) => fetchAPI<StrategyConfig>(`/strategies/${name}/config`),
  updateStrategyConfig: (name: string, params: Record<string, unknown>) =>
    fetchAPI<{ message: string }>(`/strategies/${name}/config`, {
      method: "PUT",
      body: JSON.stringify({ params }),
    }),
  assignStrategy: (name: string, marketIds: number[]) =>
    fetchAPI<{ message: string }>(`/strategies/${name}/assign`, {
      method: "POST",
      body: JSON.stringify({ market_ids: marketIds }),
    }),

  // Positions
  positions: () => fetchAPI<PositionList>("/positions"),
  closedPositions: () => fetchAPI<ClosedPositionList>("/positions/closed"),
  closePosition: (id: number) =>
    fetchAPI<{ message: string }>(`/positions/${id}/close`, { method: "POST" }),

  // Performance
  performanceSummary: () => fetchAPI<PerformanceSummary>("/performance/summary"),
  performanceHistory: () => fetchAPI<PnLPoint[]>("/performance/history"),
  performanceByStrategy: () => fetchAPI<StrategyBreakdown[]>("/performance/by-strategy"),
  performanceByCrypto: () => fetchAPI<StrategyBreakdown[]>("/performance/by-crypto"),

  // Config
  config: () => fetchAPI<Config>("/config"),
  updateConfig: (config: Partial<Config>) =>
    fetchAPI<{ message: string }>("/config", {
      method: "PUT",
      body: JSON.stringify(config),
    }),
  riskMetrics: () => fetchAPI<RiskMetrics>("/config/risk"),

  // Data
  prices: () => fetchAPI<SpotPrices>("/data/prices"),
};
