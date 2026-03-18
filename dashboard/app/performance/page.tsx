"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import type { PerformanceSummary, PnLPoint, StrategyBreakdown } from "@/lib/types";
import Header from "@/components/layout/Header";
import PnLChart from "@/components/charts/PnLChart";
import DrawdownChart from "@/components/charts/DrawdownChart";
import StatCard from "@/components/common/StatCard";
import Loading from "@/components/common/Loading";
import { formatUSD, formatPct } from "@/lib/utils";

const REFRESH = 30000;

export default function PerformancePage() {
  const { data: summary } = useSWR<PerformanceSummary>("/performance/summary", fetcher, { refreshInterval: REFRESH });
  const { data: history, isLoading } = useSWR<PnLPoint[]>("/performance/history", fetcher, { refreshInterval: REFRESH });
  const { data: byStrategy } = useSWR<StrategyBreakdown[]>("/performance/by-strategy", fetcher, { refreshInterval: REFRESH });
  const { data: byCrypto } = useSWR<StrategyBreakdown[]>("/performance/by-crypto", fetcher, { refreshInterval: REFRESH });

  return (
    <div className="flex flex-col flex-1">
      <Header title="Performance" />
      <div className="p-6 space-y-6">
        {/* Stats */}
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
          <StatCard title="Total P&L" value={summary ? formatUSD(summary.total_pnl) : "—"} />
          <StatCard title="Sharpe Ratio" value={summary ? summary.sharpe_ratio.toFixed(2) : "—"} />
          <StatCard title="Win Rate" value={summary ? formatPct(summary.win_rate) : "—"} />
          <StatCard title="Profit Factor" value={summary ? summary.profit_factor.toFixed(2) : "—"} />
          <StatCard title="Max Drawdown" value={summary ? formatPct(summary.max_drawdown) : "—"} />
        </div>

        {/* P&L Chart */}
        <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
          <h2 className="text-white font-semibold mb-4">Cumulative P&L</h2>
          {isLoading ? <Loading /> : <PnLChart data={history ?? []} />}
        </div>

        {/* Drawdown */}
        <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
          <h2 className="text-white font-semibold mb-4">Drawdown</h2>
          {isLoading ? <Loading /> : <DrawdownChart data={history ?? []} />}
        </div>

        {/* Breakdowns */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <BreakdownTable title="By Strategy" rows={byStrategy ?? []} />
          <BreakdownTable title="By Crypto" rows={byCrypto ?? []} />
        </div>
      </div>
    </div>
  );
}

function BreakdownTable({ title, rows }: { title: string; rows: StrategyBreakdown[] }) {
  return (
    <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
      <h3 className="text-white font-semibold mb-3">{title}</h3>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-gray-400 border-b border-gray-700">
            <th className="text-left py-2 font-medium">Name</th>
            <th className="text-right py-2 font-medium">Net P&L</th>
            <th className="text-right py-2 font-medium">Trades</th>
            <th className="text-right py-2 font-medium">Win Rate</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name} className="border-b border-gray-700/50">
              <td className="py-2 text-gray-200 font-mono text-xs">{r.name}</td>
              <td className={`py-2 text-right font-mono ${r.net_pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                {formatUSD(r.net_pnl)}
              </td>
              <td className="py-2 text-right text-gray-300">{r.trades}</td>
              <td className="py-2 text-right text-gray-300">{formatPct(r.win_rate)}</td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td colSpan={4} className="py-4 text-center text-gray-400">No data.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
