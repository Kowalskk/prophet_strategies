"use client";

import type { Position } from "@/lib/types";
import { formatUSD, formatDate, getPnLColor } from "@/lib/utils";
import { api } from "@/lib/api";
import { useState } from "react";

interface PositionTableProps {
  positions: Position[];
  onClose?: () => void;
}

function ageString(openedAt: string): string {
  const ms = Date.now() - new Date(openedAt).getTime();
  const h = Math.floor(ms / 3600000);
  const d = Math.floor(h / 24);
  if (d > 0) return `${d}d`;
  return `${h}h`;
}

export default function PositionTable({ positions, onClose }: PositionTableProps) {
  const [closing, setClosing] = useState<number | null>(null);

  const handleClose = async (id: number) => {
    setClosing(id);
    await api.closePosition(id);
    setClosing(null);
    onClose?.();
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-gray-400 border-b border-gray-700">
            <th className="text-left py-3 px-3 font-medium">Market</th>
            <th className="text-left py-3 px-2 font-medium">Strategy</th>
            <th className="text-left py-3 px-2 font-medium">Side</th>
            <th className="text-right py-3 px-2 font-medium">Entry</th>
            <th className="text-right py-3 px-2 font-medium">Current</th>
            <th className="text-right py-3 px-2 font-medium">Unrealized P&L</th>
            <th className="text-right py-3 px-2 font-medium">Age</th>
            <th className="text-right py-3 px-2 font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((pos) => {
            const pnl = pos.unrealized_pnl ?? pos.net_pnl ?? 0;
            return (
              <tr key={pos.id} className="border-b border-gray-700/50 hover:bg-gray-800">
                <td className="py-3 px-3 text-gray-200">Market #{pos.market_id}</td>
                <td className="py-3 px-2 text-gray-300 text-xs font-mono">{pos.strategy}</td>
                <td className="py-3 px-2">
                  <span className={`px-2 py-0.5 rounded text-xs font-bold ${
                    pos.side === "YES" ? "bg-green-900/50 text-green-400" : "bg-red-900/50 text-red-400"
                  }`}>
                    {pos.side}
                  </span>
                </td>
                <td className="py-3 px-2 text-right text-gray-300 font-mono">
                  {pos.entry_price.toFixed(3)}
                </td>
                <td className="py-3 px-2 text-right text-gray-300 font-mono">
                  {pos.current_price != null ? pos.current_price.toFixed(3) : "—"}
                </td>
                <td className={`py-3 px-2 text-right font-mono font-medium ${getPnLColor(pnl)}`}>
                  {formatUSD(pnl)}
                </td>
                <td className="py-3 px-2 text-right text-gray-400">
                  {ageString(pos.opened_at)}
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
              <td colSpan={8} className="py-8 text-center text-gray-400">
                No positions.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
