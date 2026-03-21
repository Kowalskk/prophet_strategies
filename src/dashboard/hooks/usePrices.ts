"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import type { MarketPrices } from "@/lib/types";

/** Single shared SWR key — all pages reuse the same cached response. */
export function usePrices(refreshInterval = 30000) {
  return useSWR<MarketPrices>("/markets/prices", fetcher, {
    refreshInterval,
    dedupingInterval: 15000,
  });
}

/** Pull YES and NO prices for one market_id out of the bulk map. */
export function getMarketPrice(prices: MarketPrices | undefined, marketId: number | string) {
  const entry = prices?.[String(marketId)];
  return {
    yesBid: entry?.yes?.bid ?? null,
    yesAsk: entry?.yes?.ask ?? null,
    noBid: entry?.no?.bid ?? null,
    noAsk: entry?.no?.ask ?? null,
    ts: entry?.yes?.ts ?? entry?.no?.ts ?? null,
  };
}
