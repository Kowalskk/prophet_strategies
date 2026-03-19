"use client";

import { useState } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import type { PositionList, ClosedPositionList, MarketList, Market } from "@/lib/types";
import { usePrices } from "@/hooks/usePrices";
import Header from "@/components/layout/Header";
import PositionTable from "@/components/positions/PositionTable";
import StatCard from "@/components/common/StatCard";
import Loading from "@/components/common/Loading";
import { formatUSD, formatPct } from "@/lib/utils";

const REFRESH = 15000;

export default function PositionsPage() {
  const [tab, setTab] = useState<"active" | "closed">("active");

  const { data: activeList, isLoading: loadingActive, mutate: mutateActive } =
    useSWR<PositionList>("/positions", fetcher, { refreshInterval: REFRESH });
  const { data: closedList, isLoading: loadingClosed } =
    useSWR<ClosedPositionList>("/positions/closed", fetcher, { refreshInterval: REFRESH });
  const { data: prices } = usePrices(30000);
  const { data: marketList } = useSWR<MarketList>("/markets?limit=500", fetcher, { refreshInterval: 60000 });
  const marketsMap: Record<number, Market> = {};
  for (const m of marketList?.items ?? []) marketsMap[m.id] = m;

  const active = activeList?.items ?? [];
  const closed = closedList?.items ?? [];

  const totalUnrealized = active.reduce((s, p) => s + (p.unrealized_pnl ?? 0), 0);
  const closedPnl = closed.reduce((s, p) => s + (p.net_pnl ?? 0), 0);
  const wins = closed.filter((p) => (p.net_pnl ?? 0) > 0).length;
  const winRate = closed.length > 0 ? wins / closed.length : 0;

  return (
    <div className="flex flex-col flex-1">
      <Header title="Positions" />
      <div className="p-6 space-y-6">
        {/* Summary stats */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard title="Active Positions" value={String(active.length)} />
          <StatCard title="Unrealized P&L" value={formatUSD(totalUnrealized)} />
          <StatCard title="Closed Positions" value={String(closed.length)} />
          <StatCard title="Win Rate (Closed)" value={formatPct(winRate)} subtitle={`Realized: ${formatUSD(closedPnl)}`} />
        </div>

        {/* Tabs */}
        <div className="flex gap-1 border-b border-gray-700">
          {(["active", "closed"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2 text-sm font-medium capitalize border-b-2 transition-colors ${
                tab === t
                  ? "border-blue-500 text-white"
                  : "border-transparent text-gray-400 hover:text-white"
              }`}
            >
              {t} ({t === "active" ? active.length : closed.length})
            </button>
          ))}
        </div>

        <div className="bg-gray-800 rounded-lg border border-gray-700">
          {tab === "active" && (
            loadingActive ? <Loading /> : <PositionTable positions={active} markets={marketsMap} prices={prices ?? undefined} onClose={() => mutateActive()} />
          )}
          {tab === "closed" && (
            loadingClosed ? <Loading /> : <PositionTable positions={closed} markets={marketsMap} prices={prices ?? undefined} />
          )}
        </div>
      </div>
    </div>
  );
}
