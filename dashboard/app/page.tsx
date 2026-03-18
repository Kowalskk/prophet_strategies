"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import type { PerformanceSummary, SystemStatus, PositionList, PnLPoint } from "@/lib/types";
import StatCard from "@/components/common/StatCard";
import KillSwitch from "@/components/common/KillSwitch";
import PnLChart from "@/components/charts/PnLChart";
import PositionTable from "@/components/positions/PositionTable";
import Loading from "@/components/common/Loading";
import Header from "@/components/layout/Header";
import { formatUSD, formatPct } from "@/lib/utils";
import { DollarSign, TrendingUp, Briefcase, Activity } from "lucide-react";

const REFRESH = 15000;

export default function HomePage() {
  const { data: perf } = useSWR<PerformanceSummary>("/performance/summary", fetcher, { refreshInterval: REFRESH });
  const { data: status, mutate: mutateStatus } = useSWR<SystemStatus>("/status", fetcher, { refreshInterval: REFRESH });
  const { data: positionList, mutate: mutatePositions } = useSWR<PositionList>("/positions", fetcher, { refreshInterval: REFRESH });
  const { data: history } = useSWR<PnLPoint[]>("/performance/history", fetcher, { refreshInterval: REFRESH });

  const positions = positionList?.items ?? [];
  const chartData = history ?? [];
  const last7 = chartData.slice(-7);

  return (
    <div className="flex flex-col flex-1">
      <Header title="Dashboard" />
      <div className="p-6 space-y-6">
        {/* Stat cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard
            title="Total Paper P&L"
            value={perf ? formatUSD(perf.total_pnl) : "—"}
            icon={DollarSign}
          />
          <StatCard
            title="Win Rate"
            value={perf ? formatPct(perf.win_rate) : "—"}
            subtitle={`${perf?.total_trades ?? 0} trades`}
            icon={TrendingUp}
          />
          <StatCard
            title="Active Positions"
            value={String(status?.open_positions ?? 0)}
            subtitle={`Daily P&L: ${status ? formatUSD(status.daily_pnl) : "—"}`}
            icon={Briefcase}
          />
          <StatCard
            title="System Status"
            value={status?.kill_switch ? "KILLED" : status?.scanning_active ? "Scanning" : "Idle"}
            subtitle={status?.last_scan_at ? `Last scan: ${new Date(status.last_scan_at).toLocaleTimeString()}` : undefined}
            icon={Activity}
          />
        </div>

        {/* P&L Chart */}
        <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-white font-semibold">P&L (Last 7 Days)</h2>
          </div>
          {chartData.length === 0 ? <Loading /> : <PnLChart data={last7} />}
        </div>

        {/* Top positions + kill switch */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="lg:col-span-2 bg-gray-800 rounded-lg p-4 border border-gray-700">
            <h2 className="text-white font-semibold mb-3">Active Positions (Top 5)</h2>
            {positions.length === 0 ? (
              <p className="text-gray-400 text-sm">No active positions.</p>
            ) : (
              <PositionTable
                positions={positions.slice(0, 5)}
                onClose={() => { mutatePositions(); mutateStatus(); }}
              />
            )}
          </div>
          <div className="bg-gray-800 rounded-lg p-4 border border-gray-700 flex flex-col items-center justify-center gap-4">
            <p className="text-gray-400 text-sm text-center">Emergency stop — halts all new order placement</p>
            <KillSwitch
              isActive={status?.kill_switch ?? false}
              onToggle={() => mutateStatus()}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
