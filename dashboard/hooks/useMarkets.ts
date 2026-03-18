"use client";

import useSWR from "swr";

import { api } from "@/lib/api";
import type { Market, MarketList, OrderBook } from "@/lib/types";

// ---------------------------------------------------------------------------
// useMarkets — list of markets, optionally filtered by crypto and status
// ---------------------------------------------------------------------------

export function useMarkets(crypto?: string, status?: string) {
  const key = `/markets${crypto && crypto !== "All" ? `?crypto=${crypto}` : ""}${
    status ? `${crypto ? "&" : "?"}status=${status}` : ""
  }`;

  const { data, error, isLoading, mutate } = useSWR<MarketList | null>(
    key,
    () => api.markets(crypto),
    { refreshInterval: 60_000 }
  );

  return {
    markets: data?.items ?? [],
    total: data?.total ?? 0,
    isLoading,
    error,
    mutate,
  };
}

// ---------------------------------------------------------------------------
// useMarket — single market by condition_id (string) or numeric id
// ---------------------------------------------------------------------------

export function useMarket(conditionId: string) {
  const numericId = parseInt(conditionId, 10);
  const isNumeric = !isNaN(numericId);

  const { data, error, isLoading } = useSWR<Market | null>(
    conditionId ? `/markets/${conditionId}` : null,
    () => (isNumeric ? api.market(numericId) : null),
    { refreshInterval: 30_000 }
  );

  return { market: data ?? null, isLoading, error };
}

// ---------------------------------------------------------------------------
// useOrderBook — order book for a market (refreshes every 30s)
// ---------------------------------------------------------------------------

export function useOrderBook(conditionId: string) {
  const numericId = parseInt(conditionId, 10);
  const isNumeric = !isNaN(numericId) && numericId > 0;

  const { data, error, isLoading } = useSWR<OrderBook | null>(
    isNumeric ? `/markets/${numericId}/orderbook` : null,
    () => (isNumeric ? api.marketOrderBook(numericId) : null),
    { refreshInterval: 30_000 }
  );

  return { orderBook: data ?? null, isLoading, error };
}
