"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import useSWR from "swr";

import { api } from "@/lib/api";
import type { SystemStatus, SpotPrices } from "@/lib/types";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const API_URL = "/api/v1";
// We still try to derive WS_BASE_URL, but it will likely fail on HTTPS Vercel
// unless the backend has WSS.
const WS_BASE_URL = typeof window !== "undefined" 
  ? `ws://${window.location.host}` 
  : "ws://localhost:8000";

const BACKOFF_STEPS = [1_000, 2_000, 4_000, 8_000, 16_000, 30_000];

// ---------------------------------------------------------------------------
// useWebSocket — generic reconnecting WebSocket hook
// ---------------------------------------------------------------------------

function useWebSocket<T>(
  path: string,
  onMessage: (data: T) => void
): { isConnected: boolean } {
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const backoffRef = useRef(0);
  const mountedRef = useRef(true);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;

    const url = `${WS_BASE_URL}${path}`;
    let ws: WebSocket;

    try {
      ws = new WebSocket(url);
    } catch {
      // Browser blocked or WS not available — give up silently
      return;
    }

    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) return;
      setIsConnected(true);
      backoffRef.current = 0; // Reset backoff on successful connection
    };

    ws.onmessage = (evt) => {
      if (!mountedRef.current) return;
      try {
        const data: T = JSON.parse(evt.data);
        onMessage(data);
      } catch {
        // Ignore malformed frames
      }
    };

    ws.onerror = () => {
      setIsConnected(false);
    };

    ws.onclose = () => {
      if (!mountedRef.current) return;
      setIsConnected(false);

      // Exponential backoff reconnect
      const delay =
        BACKOFF_STEPS[Math.min(backoffRef.current, BACKOFF_STEPS.length - 1)];
      backoffRef.current += 1;
      setTimeout(connect, delay);
    };
  }, [path, onMessage]);

  useEffect(() => {
    mountedRef.current = true;
    connect();

    return () => {
      mountedRef.current = false;
      wsRef.current?.close();
    };
  }, [connect]);

  return { isConnected };
}

// ---------------------------------------------------------------------------
// useSystemStatus — live system status with WS → SWR fallback
// ---------------------------------------------------------------------------

export function useSystemStatus() {
  const [wsStatus, setWsStatus] = useState<SystemStatus | null>(null);
  const [wsConnected, setWsConnectedState] = useState(false);

  const handleWsMessage = useCallback((data: SystemStatus) => {
    setWsStatus(data);
    setWsConnectedState(true);
  }, []);

  // Try WebSocket
  const { isConnected } = useWebSocket<SystemStatus>(
    "/ws/status",
    handleWsMessage
  );

  // SWR fallback — only poll when WS is not connected
  const { data: swrStatus } = useSWR<SystemStatus | null>(
    !isConnected ? "/status" : null,
    () => api.status(),
    { refreshInterval: 10_000 }
  );

  return {
    status: wsStatus ?? swrStatus ?? null,
    isConnected,
  };
}

// ---------------------------------------------------------------------------
// useLivePrices — live spot prices with WS → SWR fallback
// ---------------------------------------------------------------------------

export function useLivePrices() {
  const [wsPrices, setWsPrices] = useState<Record<string, number>>({});
  const [isLive, setIsLive] = useState(false);

  const handleWsMessage = useCallback((data: Record<string, number>) => {
    setWsPrices(data);
    setIsLive(true);
  }, []);

  const { isConnected } = useWebSocket<Record<string, number>>(
    "/ws/prices",
    handleWsMessage
  );

  // SWR fallback when no WS
  const { data: swrPrices } = useSWR<SpotPrices | null>(
    !isConnected ? "/data/prices" : null,
    () => api.prices(),
    { refreshInterval: 30_000 }
  );

  // Convert SpotPrices array to Record<symbol, price>
  const fallbackPrices: Record<string, number> =
    swrPrices?.prices.reduce(
      (acc, p) => ({ ...acc, [p.crypto]: p.price_usd }),
      {} as Record<string, number>
    ) ?? {};

  return {
    prices: isConnected ? wsPrices : fallbackPrices,
    isLive: isConnected && isLive,
  };
}
