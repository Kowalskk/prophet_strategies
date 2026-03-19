"use client";

import useSWR from "swr";
import { useState } from "react";
import { fetcher } from "@/lib/api";
import type { SignalList, SignalSummaryItem } from "@/lib/types";
import { usePrices, getMarketPrice } from "@/hooks/usePrices";
import Header from "@/components/layout/Header";
import Loading from "@/components/common/Loading";
import PriceBadge from "@/components/common/PriceBadge";
import { formatUSD } from "@/lib/utils";
import { Activity, TrendingUp, TrendingDown } from "lucide-react";

const REFRESH = 10000;
const LIMIT = 100;

const STATUS_COLORS: Record<string, string> = {
  pending: "text-yellow-400 bg-yellow-400/10 border-yellow-400/20",
  filled: "text-green-400 bg-green-400/10 border-green-400/20",
  cancelled: "text-slate-400 bg-slate-400/10 border-slate-400/20",
  rejected: "text-red-400 bg-red-400/10 border-red-400/20",
};

export default function SignalsPage() {
  const [strategyFilter, setStrategyFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [offset, setOffset] = useState(0);

  const qs = new URLSearchParams();
  if (strategyFilter) qs.set("strategy", strategyFilter);
  if (statusFilter) qs.set("status", statusFilter);
  qs.set("limit", String(LIMIT));
  qs.set("offset", String(offset));

  const { data: signalList } = useSWR<SignalList>(
    `/signals?${qs.toString()}`,
    fetcher,
    { refreshInterval: REFRESH }
  );
  const { data: summary } = useSWR<SignalSummaryItem[]>(
    "/signals/summary",
    fetcher,
    { refreshInterval: REFRESH }
  );
  const { data: prices } = usePrices(30000);

  const signals = signalList?.items ?? [];
  const total = signalList?.total ?? 0;

  // Aggregate summary stats
  const totalSignals = summary?.reduce((s, x) => s + x.count, 0) ?? 0;
  const pendingCount = summary?.filter(x => x.status === "pending").reduce((s, x) => s + x.count, 0) ?? 0;
  const filledCount = summary?.filter(x => x.status === "filled").reduce((s, x) => s + x.count, 0) ?? 0;

  const strategies = Array.from(new Set(summary?.map(x => x.strategy) ?? [])).sort();

  return (
    <div className="flex flex-col flex-1">
      <Header title="Signals" />
      <div className="p-6 space-y-6">

        {/* Summary cards */}
        <div className="grid grid-cols-3 gap-4">
          <div className="glass p-5 card-hover">
            <p className="text-slate-400 text-xs uppercase tracking-widest mb-1">Total Signals</p>
            <p className="text-white text-3xl font-bold">{totalSignals.toLocaleString()}</p>
          </div>
          <div className="glass p-5 card-hover">
            <p className="text-yellow-400 text-xs uppercase tracking-widest mb-1">Pending</p>
            <p className="text-white text-3xl font-bold">{pendingCount.toLocaleString()}</p>
          </div>
          <div className="glass p-5 card-hover">
            <p className="text-green-400 text-xs uppercase tracking-widest mb-1">Filled</p>
            <p className="text-white text-3xl font-bold">{filledCount.toLocaleString()}</p>
          </div>
        </div>

        {/* Strategy breakdown */}
        {summary && summary.length > 0 && (
          <div className="glass p-6 card-hover">
            <h2 className="text-white font-bold text-base mb-4">By Strategy</h2>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-slate-400 text-xs uppercase tracking-widest border-b border-white/5">
                    <th className="text-left pb-3 pr-6">Strategy</th>
                    {Array.from(new Set(summary.map(x => x.status))).sort().map(s => (
                      <th key={s} className="text-right pb-3 px-4 capitalize">{s}</th>
                    ))}
                    <th className="text-right pb-3 pl-4">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {strategies.map(strat => {
                    const rows = summary.filter(x => x.strategy === strat);
                    const statuses = Array.from(new Set(summary.map(x => x.status))).sort();
                    const stratTotal = rows.reduce((s, x) => s + x.count, 0);
                    return (
                      <tr key={strat} className="border-b border-white/5 hover:bg-white/3">
                        <td className="py-3 pr-6 text-yellow-400 font-mono text-xs">{strat}</td>
                        {statuses.map(s => {
                          const row = rows.find(r => r.status === s);
                          return (
                            <td key={s} className="py-3 px-4 text-right text-slate-300">
                              {row ? row.count.toLocaleString() : "—"}
                            </td>
                          );
                        })}
                        <td className="py-3 pl-4 text-right text-white font-semibold">{stratTotal.toLocaleString()}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Signal feed */}
        <div className="glass p-6 card-hover">
          <div className="flex items-center justify-between mb-4 gap-4 flex-wrap">
            <h2 className="text-white font-bold text-base">Signal Feed</h2>
            <div className="flex gap-3 flex-wrap">
              <select
                value={strategyFilter}
                onChange={e => { setStrategyFilter(e.target.value); setOffset(0); }}
                className="bg-white/5 border border-white/10 rounded-lg px-3 py-1.5 text-sm text-slate-300 focus:outline-none focus:border-yellow-500/50"
              >
                <option value="">All Strategies</option>
                {strategies.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
              <select
                value={statusFilter}
                onChange={e => { setStatusFilter(e.target.value); setOffset(0); }}
                className="bg-white/5 border border-white/10 rounded-lg px-3 py-1.5 text-sm text-slate-300 focus:outline-none focus:border-yellow-500/50"
              >
                <option value="">All Statuses</option>
                <option value="pending">Pending</option>
                <option value="filled">Filled</option>
                <option value="cancelled">Cancelled</option>
                <option value="rejected">Rejected</option>
              </select>
            </div>
          </div>

          {!signalList ? (
            <Loading />
          ) : signals.length === 0 ? (
            <p className="text-slate-400 text-sm py-8 text-center italic">No signals match the current filters.</p>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-slate-400 text-xs uppercase tracking-widest border-b border-white/5">
                      <th className="text-left pb-3 pr-4">Time</th>
                      <th className="text-left pb-3 pr-4">Strategy</th>
                      <th className="text-left pb-3 pr-4">Market</th>
                      <th className="text-left pb-3 pr-4">Side</th>
                      <th className="text-right pb-3 pr-4">Target Price</th>
                      <th className="text-center pb-3 pr-4">Bid / Ask</th>
                      <th className="text-right pb-3 pr-4">Size</th>
                      <th className="text-right pb-3 pr-4">Confidence</th>
                      <th className="text-right pb-3">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {signals.map(sig => (
                      <tr key={sig.id} className="border-b border-white/5 hover:bg-white/3 transition-colors">
                        <td className="py-2.5 pr-4 text-slate-400 text-xs whitespace-nowrap">
                          {new Date(sig.created_at).toLocaleString()}
                        </td>
                        <td className="py-2.5 pr-4 text-yellow-400 font-mono text-xs whitespace-nowrap">
                          {sig.strategy}
                        </td>
                        <td className="py-2.5 pr-4 max-w-[200px]">
                          <div className="text-slate-300 text-xs truncate">
                            {sig.market?.crypto ?? `#${sig.market_id}`}
                            {sig.market?.threshold != null && (
                              <span className="text-slate-500 ml-1">
                                {sig.market.direction === "above" ? "≥" : "≤"}${sig.market.threshold.toLocaleString()}
                              </span>
                            )}
                          </div>
                          {sig.market?.resolution_date && (
                            <div className="text-slate-500 text-[10px]">
                              exp {new Date(sig.market.resolution_date).toLocaleDateString()}
                            </div>
                          )}
                        </td>
                        <td className="py-2.5 pr-4">
                          <span className={`flex items-center gap-1 text-xs font-semibold ${sig.side === "YES" ? "text-green-400" : "text-red-400"}`}>
                            {sig.side === "YES" ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
                            {sig.side}
                          </span>
                        </td>
                        <td className="py-2.5 pr-4 text-right text-slate-300 font-mono text-xs">
                          {(sig.target_price * 100).toFixed(1)}¢
                        </td>
                        <td className="py-2.5 pr-4 text-center">
                          {(() => {
                            const mp = getMarketPrice(prices, sig.market_id);
                            const bid = sig.side === "YES" ? mp.yesBid : mp.noBid;
                            const ask = sig.side === "YES" ? mp.yesAsk : mp.noAsk;
                            return <PriceBadge bid={bid} ask={ask} side={sig.side as "YES" | "NO"} />;
                          })()}
                        </td>
                        <td className="py-2.5 pr-4 text-right text-slate-300 text-xs">
                          {formatUSD(sig.size_usd)}
                        </td>
                        <td className="py-2.5 pr-4 text-right text-xs">
                          <div className="flex items-center justify-end gap-1">
                            <div className="w-16 bg-white/10 rounded-full h-1.5">
                              <div
                                className="bg-yellow-400 h-1.5 rounded-full"
                                style={{ width: `${(sig.confidence * 100).toFixed(0)}%` }}
                              />
                            </div>
                            <span className="text-slate-400">{(sig.confidence * 100).toFixed(0)}%</span>
                          </div>
                        </td>
                        <td className="py-2.5 text-right">
                          <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-bold border uppercase tracking-wider ${STATUS_COLORS[sig.status] ?? "text-slate-400 bg-slate-400/10 border-slate-400/20"}`}>
                            {sig.status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              <div className="flex items-center justify-between mt-4 text-xs text-slate-400">
                <span>Showing {offset + 1}–{Math.min(offset + LIMIT, total)} of {total.toLocaleString()}</span>
                <div className="flex gap-2">
                  <button
                    disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - LIMIT))}
                    className="px-3 py-1 rounded bg-white/5 border border-white/10 disabled:opacity-30 hover:bg-white/10 transition-colors"
                  >
                    Prev
                  </button>
                  <button
                    disabled={offset + LIMIT >= total}
                    onClick={() => setOffset(offset + LIMIT)}
                    className="px-3 py-1 rounded bg-white/5 border border-white/10 disabled:opacity-30 hover:bg-white/10 transition-colors"
                  >
                    Next
                  </button>
                </div>
              </div>
            </>
          )}
        </div>

      </div>
    </div>
  );
}
