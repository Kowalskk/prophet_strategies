"use client";

import useSWR from "swr";

import { api } from "@/lib/api";
import type { ClosedPositionList, Position, PositionList } from "@/lib/types";

// ---------------------------------------------------------------------------
// useOpenPositions — real-time open positions, refreshed every 15s
// ---------------------------------------------------------------------------

export function useOpenPositions() {
  const { data, error, isLoading, mutate } = useSWR<PositionList | null>(
    "/positions",
    () => api.positions(),
    { refreshInterval: 15_000 }
  );

  return {
    positions: data?.items ?? [],
    total: data?.total ?? 0,
    isLoading,
    error,
    mutate,
  };
}

// ---------------------------------------------------------------------------
// useClosedPositions — paginated closed positions
// ---------------------------------------------------------------------------

export function useClosedPositions(limit = 50, offset = 0) {
  const key = `/positions/closed?limit=${limit}&offset=${offset}`;

  const { data, error, isLoading } = useSWR<ClosedPositionList | null>(
    key,
    () => api.closedPositions(),
    { refreshInterval: 60_000 }
  );

  return {
    positions: data?.items ?? [],
    total: data?.total ?? 0,
    isLoading,
    error,
  };
}

// ---------------------------------------------------------------------------
// usePositionCount — lightweight count of open positions
// ---------------------------------------------------------------------------

export function usePositionCount(): number {
  const { data } = useSWR<PositionList | null>(
    "/positions",
    () => api.positions(),
    { refreshInterval: 15_000 }
  );

  return data?.total ?? 0;
}
