"use client";

import { useState } from "react";
import type { Position, Market, MarketPrices } from "@/lib/types";
import { formatUSD, getPnLColor } from "@/lib/utils";
import { getMarketPrice } from "@/hooks/usePrices";
import { api } from "@/lib/api";
import PriceBadge from "@/components/common/PriceBadge";
import { ArrowUpDown, ArrowUp, ArrowDown } from "lucide-react";

interface PositionTableProps {
  positions: Position[];
  markets?: Record<number, Market>;
  prices?: MarketPrices;
  onClose?: () => void;
}

type SortKey = "market" | "strategy" | "side" | "entry" | "current" | "pnl" | "age";
type SortDir = "asc" | "desc";

function ageMs(openedAt: string): number {
  return Date.now() - new Date(openedAt).getTime();
}

function ageString(ms: number): string {
  const h = Math.floor(ms / 3600000);
  const d = Math.floor(h / 24);
  if (d > 0) return `${d}d`;
  return `${h}h`;
}

function marketLabel(market?: Market): string {
  if (!market) return "";
  if (market.crypto && market.threshold != null) {
    const dir = (market.direction ?? "above") === "above" ? ">" : "<";
    return `${market.crypto} ${dir}$${market.threshold.toLocaleString()}`;
  }
  return market.question?.slice(0, 45) ?? "";
}

export default function PositionTable({ positions, markets, prices, onClose }: PositionTableProps) {
  const [closing, setClosing] = useState<number | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("age");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const handleClose = async (id: number) => {
    setClosing(id);
    await api.closePosition(id);
    setClosing(null);
    onClose?.();
  };

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir(d => d === "asc" ? "desc" : "asc");
    else { setSortKey(key); setSortDir("asc"); }
  };

  const sorted = [...positions].sort((a, b) => {
    let va: number | string = 0, vb: number | string = 0;
    switch (sortKey) {
      case "market":   va = marketLabel(markets?.[a.market_id]); vb = marketLabel(markets?.[b.market_id]); break;
      case "strategy": va = a.strategy; vb = b.strategy; break;
      case "side":     va = a.side; vb = b.side; break;
      case "entry":    va = a.entry_price; vb = b.entry_price; break;
      case "current":  va = a.current_price ?? -999; vb = b.current_price ?? -999; break;
      case "pnl":      va = a.unrealized_pnl ?? a.net_pnl ?? 0; vb = b.unrealized_pnl ?? b.net_pnl ?? 0; break;
      case "age":      va = ageMs(a.opened_at); vb = ageMs(b.opened_at); break;
    }
    if (typeof va === "string") { const c = va.localeCompare(vb as string); return sortDir === "asc" ? c : -c; }
    return sortDir === "asc" ? (va as number) - (vb as number) : (vb as number) - (va as number);
  });

  const SortIcon = ({ col }: { col: SortKey }) => {
    if (sortKey !== col) return <ArrowUpDown className="inline h-3 w-3 ml-0.5 opacity-30" />;
    return sortDir === "asc"
      ? <ArrowUp className="inline h-3 w-3 ml-0.5 text-yellow-400" />
      : <ArrowDown className="inline h-3 w-3 ml-0.5 text-yellow-400" />;
  };

  const Th = ({ col, label, cls }: { col: SortKey; label: string; cls?: string }) => (
    <th
      className={`py-3 px-2 font-medium cursor-pointer select-none hover:text-white transition-colors ${cls ?? ""}`}
      onClick={() => toggleSort(col)}
    >
      {label} <SortIcon col={col} />
    </th>
  );

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-gray-400 border-b border-gray-700 text-xs uppercase tracking-wide">
            <Th col="market" label="Market" cls="text-left px-3" />
            <Th col="strategy" label="Strategy" cls="text-left" />
            <Th col="side" label="Side" cls="text-left" />
            <Th col="entry" label="Entry" cls="text-right" />
            <Th col="current" label="Current" cls="text-right" />
            <th className="text-center py-3 px-2 font-medium text-xs uppercase tracking-wide">Bid / Ask</th>
            <Th col="pnl" label="P&L" cls="text-right" />
            <Th col="age" label="Age" cls="text-right" />
            <th className="text-right py-3 px-2 font-medium text-xs uppercase tracking-wide">Actions</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((pos) => {
            const pnl = pos.unrealized_pnl ?? pos.net_pnl ?? 0;
            const mp = getMarketPrice(prices, pos.market_id);
            const bid = pos.side === "YES" ? mp.yesBid : mp.noBid;
            const ask = pos.side === "YES" ? mp.yesAsk : mp.noAsk;
            const market = markets?.[pos.market_id];
            const label = marketLabel(market);

            return (
              <tr key={pos.id} className="border-b border-gray-700/50 hover:bg-gray-700/30 transition-colors">
                <td className="py-3 px-3 max-w-[210px]">
                  {label ? (
                    <a
                      href="/markets"
                      className="hover:text-yellow-400 text-gray-200 transition-colors block text-xs font-medium truncate"
                      title={market?.question ?? label}
                    >
                      {label}
                    </a>
                  ) : (
                    <span className="text-gray-500 text-xs">Market #{pos.market_id}</span>
                  )}
                  {market?.resolution_date && (
                    <div className="text-gray-500 text-[10px] mt-0.5">
                      exp {new Date(market.resolution_date).toLocaleDateString()}
                    </div>
                  )}
                </td>
                <td className="py-3 px-2 text-gray-300 text-xs font-mono">{pos.strategy}</td>
                <td className="py-3 px-2">
                  <span className={`px-2 py-0.5 rounded text-xs font-bold ${
                    pos.side === "YES" ? "bg-green-900/50 text-green-400" : "bg-red-900/50 text-red-400"
                  }`}>{pos.side}</span>
                </td>
                <td className="py-3 px-2 text-right text-gray-300 font-mono text-xs">
                  {(pos.entry_price * 100).toFixed(2)}c
                </td>
                <td className="py-3 px-2 text-right font-mono text-xs">
                  {pos.current_price != null
                    ? <span className={getPnLColor(pos.current_price - pos.entry_price)}>{(pos.current_price * 100).toFixed(2)}c</span>
                    : <span className="text-gray-500">--</span>}
                </td>
                <td className="py-3 px-2 text-center">
                  <PriceBadge bid={bid} ask={ask} side={pos.side as "YES" | "NO"} />
                </td>
                <td className={`py-3 px-2 text-right font-mono font-medium text-xs ${getPnLColor(pnl)}`}>
                  {formatUSD(pnl)}
                </td>
                <td className="py-3 px-2 text-right text-gray-400 text-xs">
                  {ageString(ageMs(pos.opened_at))}
                </td>
                <td className="py-3 px-2 text-right">
                  {pos.status === "open" && (
                    <button
                      onClick={() => handleClose(pos.id)}
                      disabled={closing === pos.id}
                      className="px-2 py-1 bg-red-800 hover:bg-red-700 text-red-200 text-xs rounded disabled:opacity-50"
                    >
                      {closing === pos.id ? "..." : "Close"}
                    </button>
                  )}
                  {pos.status === "closed" && (
                    <span className="text-gray-500 text-xs">{pos.exit_reason ?? "closed"}</span>
                  )}
                </td>
              </tr>
            );
          })}
          {positions.length === 0 && (
            <tr>
              <td colSpan={9} className="py-8 text-center text-gray-400">No positions.</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
