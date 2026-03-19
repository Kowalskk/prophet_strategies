"use client";

import useSWR from "swr";
import Link from "next/link";
import { fetcher } from "@/lib/api";
import type { PerformanceSummary, SystemStatus, PositionList, PnLPoint, SignalList, SignalSummaryItem } from "@/lib/types";
import { usePrices } from "@/hooks/usePrices";
import StatCard from "@/components/common/StatCard";
import KillSwitch from "@/components/common/KillSwitch";
import PnLChart from "@/components/charts/PnLChart";
import PositionTable from "@/components/positions/PositionTable";
import Loading from "@/components/common/Loading";
import Header from "@/components/layout/Header";
import { formatUSD, formatPct } from "@/lib/utils";
import { DollarSign, TrendingUp, TrendingDown, Briefcase, Activity, Radio, ArrowRight } from "lucide-react";

const REFRESH = 15000;

export default function HomePage() {
  const { data: perf } = useSWR<PerformanceSummary>("/performance/summary", fetcher, { refreshInterval: REFRESH });
  const { data: status, mutate: mutateStatus } = useSWR<SystemStatus>("/status", fetcher, { refreshInterval: REFRESH });
  const { data: positionList, mutate: mutatePositions } = useSWR<PositionList>("/positions", fetcher, { refreshInterval: REFRESH });
  const { data: history } = useSWR<PnLPoint[]>("/performance/history", fetcher, { refreshInterval: REFRESH });
  const { data: recentSignals } = useSWR<SignalList>("/signals?limit=10", fetcher, { refreshInterval: REFRESH });
  const { data: prices } = usePrices(30000);
  const { data: signalSummary } = useSWR<SignalSummaryItem[]>("/signals/summary", fetcher, { refreshInterval: REFRESH });

  const positions = positionList?.items ?? [];
  const chartData = history ?? [];
  const last7 = chartData.slice(-7);

  const totalSignals = signalSummary?.reduce((s, x) => s + x.count, 0) ?? 0;
  const pendingSignals = signalSummary?.filter(x => x.status === "pending").reduce((s, x) => s + x.count, 0) ?? 0;
  const filledSignals = signalSummary?.filter(x => x.status === "filled").reduce((s, x) => s + x.count, 0) ?? 0;

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

        {/* Signals summary row */}
        <div className="grid grid-cols-3 gap-4">
          <div className="glass p-4 card-hover flex items-center gap-4">
            <div className="bg-yellow-500/10 p-3 rounded-xl border border-yellow-500/20">
              <Radio className="h-5 w-5 text-yellow-400" />
            </div>
            <div>
              <p className="text-slate-400 text-xs uppercase tracking-widest">Total Signals</p>
              <p className="text-white text-2xl font-bold">{totalSignals.toLocaleString()}</p>
            </div>
          </div>
          <div className="glass p-4 card-hover flex items-center gap-4">
            <div className="bg-yellow-500/10 p-3 rounded-xl border border-yellow-500/20">
              <Activity className="h-5 w-5 text-yellow-400" />
            </div>
            <div>
              <p className="text-yellow-400 text-xs uppercase tracking-widest">Pending</p>
              <p className="text-white text-2xl font-bold">{pendingSignals.toLocaleString()}</p>
            </div>
          </div>
          <div className="glass p-4 card-hover flex items-center gap-4">
            <div className="bg-green-500/10 p-3 rounded-xl border border-green-500/20">
              <TrendingUp className="h-5 w-5 text-green-400" />
            </div>
            <div>
              <p className="text-green-400 text-xs uppercase tracking-widest">Filled</p>
              <p className="text-white text-2xl font-bold">{filledSignals.toLocaleString()}</p>
            </div>
          </div>
        </div>

        {/* P&L Chart + Recent Signals */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 glass p-6 card-hover">
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-white font-bold text-lg tracking-tight">System Performance (Last 7 Days)</h2>
            </div>
            {chartData.length === 0 ? <Loading /> : <PnLChart data={last7} />}
          </div>

          {/* Recent Signals */}
          <div className="glass p-6 card-hover flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-white font-bold text-base">Recent Signals</h2>
              <Link href="/signals" className="text-yellow-400 text-xs flex items-center gap-1 hover:text-yellow-300 transition-colors">
                View all <ArrowRight className="h-3 w-3" />
              </Link>
            </div>
            {!recentSignals ? (
              <Loading />
            ) : recentSignals.items.length === 0 ? (
              <p className="text-slate-400 text-sm py-4 text-center italic">No signals yet.</p>
            ) : (
              <div className="space-y-2 flex-1 overflow-hidden">
                {recentSignals.items.map(sig => (
                  <div key={sig.id} className="flex items-center justify-between py-2 border-b border-white/5 last:border-0">
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="text-yellow-400 font-mono text-[10px]">{sig.strategy}</span>
                        <span className={`text-[10px] font-bold ${sig.side === "YES" ? "text-green-400" : "text-red-400"}`}>
                          {sig.side === "YES" ? <TrendingUp className="inline h-3 w-3" /> : <TrendingDown className="inline h-3 w-3" />}
                          {" "}{sig.side}
                        </span>
                      </div>
                      <div className="text-slate-400 text-[10px] truncate">
                        {sig.market?.crypto ?? `#${sig.market_id}`}
                        {sig.market?.threshold != null && ` ${sig.market.direction === "above" ? "≥" : "≤"}$${sig.market.threshold.toLocaleString()}`}
                      </div>
                    </div>
                    <div className="text-right shrink-0 ml-2">
                      <div className="text-slate-300 text-xs font-mono">{(sig.target_price * 100).toFixed(1)}¢</div>
                      <span className={`text-[10px] font-bold uppercase ${
                        sig.status === "pending" ? "text-yellow-400" :
                        sig.status === "filled" ? "text-green-400" : "text-slate-400"
                      }`}>{sig.status}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Top positions + kill switch */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 glass p-6 card-hover">
            <h2 className="text-white font-bold text-lg tracking-tight mb-4 text-gold">Active Positions (Top 5)</h2>
            {positions.length === 0 ? (
              <p className="text-slate-400 text-sm py-8 text-center italic">No active market exposure detected.</p>
            ) : (
              <PositionTable
                positions={positions.slice(0, 5)}
                prices={prices ?? undefined}
                onClose={() => { mutatePositions(); mutateStatus(); }}
              />
            )}
          </div>
          <div className="glass p-8 card-hover flex flex-col items-center justify-center gap-6 text-center">
            <div className="bg-red-500/10 p-4 rounded-full border border-red-500/20">
              <Activity className="h-8 w-8 text-red-500" />
            </div>
            <div>
              <h3 className="text-white font-bold text-lg mb-2">Emergency Protocol</h3>
              <p className="text-slate-400 text-sm max-w-[200px]">Immediate halt of all strategy scanning and order placement</p>
            </div>
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
