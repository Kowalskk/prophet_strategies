"use client";

import useSWR from "swr";

import { api } from "@/lib/api";
import type {
  PerformanceSummary,
  PnLPoint,
  StrategyBreakdown,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// usePerformanceSummary — overall stats, refreshed every 60s
// ---------------------------------------------------------------------------

export function usePerformanceSummary() {
  const { data, error, isLoading } = useSWR<PerformanceSummary | null>(
    "/performance/summary",
    () => api.performanceSummary(),
    { refreshInterval: 60_000 }
  );

  return { summary: data ?? null, isLoading, error };
}

// ---------------------------------------------------------------------------
// usePnLHistory — daily P&L series for charting
// ---------------------------------------------------------------------------

export function usePnLHistory(days = 30) {
  const { data, error, isLoading } = useSWR<PnLPoint[] | null>(
    `/performance/history?days=${days}`,
    () => api.performanceHistory(),
    { refreshInterval: 60_000 }
  );

  return { history: data ?? [], isLoading, error };
}

// ---------------------------------------------------------------------------
// useStrategyBreakdown — P&L grouped by strategy
// ---------------------------------------------------------------------------

export function useStrategyBreakdown() {
  const { data, error, isLoading } = useSWR<StrategyBreakdown[] | null>(
    "/performance/by-strategy",
    () => api.performanceByStrategy(),
    { refreshInterval: 120_000 }
  );

  return { breakdown: data ?? [], isLoading, error };
}

// ---------------------------------------------------------------------------
// useCryptoBreakdown — P&L grouped by crypto (BTC/ETH/SOL)
// ---------------------------------------------------------------------------

export function useCryptoBreakdown() {
  const { data, error, isLoading } = useSWR<StrategyBreakdown[] | null>(
    "/performance/by-crypto",
    () => api.performanceByCrypto(),
    { refreshInterval: 120_000 }
  );

  return { breakdown: data ?? [], isLoading, error };
}
