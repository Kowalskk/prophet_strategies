"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import type { SystemStatus, SpotPrices } from "@/lib/types";

const CRYPTO_COLORS: Record<string, string> = {
  BTC: "text-orange-400",
  ETH: "text-blue-400",
  SOL: "text-purple-400",
};

interface HeaderProps {
  title: string;
}

export default function Header({ title }: HeaderProps) {
  const { data: status } = useSWR<SystemStatus>("/status", fetcher, { refreshInterval: 15000 });
  const { data: spotData } = useSWR<SpotPrices>("/data/prices", fetcher, {
    refreshInterval: 30000,
    dedupingInterval: 20000,
  });

  const isRunning = status && !status.kill_switch;

  return (
    <header className="h-14 bg-gray-900 border-b border-gray-700 flex items-center justify-between px-6 gap-4">
      <h1 className="text-white font-semibold text-lg shrink-0">{title}</h1>

      {/* Spot price ticker */}
      <div className="flex items-center gap-4 overflow-x-auto">
        {spotData?.prices.map((p) => (
          <div key={p.crypto} className="flex items-center gap-1.5 shrink-0">
            <span className={`text-xs font-bold ${CRYPTO_COLORS[p.crypto] ?? "text-slate-400"}`}>
              {p.crypto}
            </span>
            <span className="text-white text-sm font-mono font-semibold">
              ${p.price_usd >= 1000
                ? p.price_usd.toLocaleString("en-US", { maximumFractionDigits: 0 })
                : p.price_usd.toLocaleString("en-US", { maximumFractionDigits: 2 })}
            </span>
          </div>
        ))}
      </div>

      <div className="flex items-center gap-3 shrink-0">
        <span className="bg-yellow-500/20 text-yellow-400 text-xs font-bold px-3 py-1 rounded-full border border-yellow-500/50">
          PAPER TRADING
        </span>
        <div className="flex items-center gap-2">
          <div className={`w-2.5 h-2.5 rounded-full ${isRunning ? "bg-green-400" : "bg-red-400"}`} />
          <span className="text-gray-400 text-sm">
            {status?.kill_switch ? "Kill Switch Active" : isRunning ? "Running" : "Connecting..."}
          </span>
        </div>
      </div>
    </header>
  );
}
