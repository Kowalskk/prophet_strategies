"use client";

import { useState } from "react";
import type { Market } from "@/lib/types";
import { formatDate } from "@/lib/utils";

interface MarketTableProps {
  markets: Market[];
  onRowClick?: (market: Market) => void;
  selectedId?: number;
}

type SortKey = "resolution_date" | "crypto" | "status";

export default function MarketTable({ markets, onRowClick, selectedId }: MarketTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>("resolution_date");
  const [sortAsc, setSortAsc] = useState(true);

  const sorted = [...markets].sort((a, b) => {
    const av = a[sortKey] ?? "";
    const bv = b[sortKey] ?? "";
    return sortAsc ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
  });

  const toggle = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(true); }
  };

  const SortBtn = ({ k, label }: { k: SortKey; label: string }) => (
    <button onClick={() => toggle(k)} className="flex items-center gap-1 hover:text-white">
      {label}
      {sortKey === k && <span>{sortAsc ? "↑" : "↓"}</span>}
    </button>
  );

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-gray-400 border-b border-gray-700">
            <th className="text-left py-3 px-3 font-medium">Question</th>
            <th className="text-left py-3 px-2 font-medium">
              <SortBtn k="crypto" label="Crypto" />
            </th>
            <th className="text-left py-3 px-2 font-medium">
              <SortBtn k="resolution_date" label="Resolves" />
            </th>
            <th className="text-left py-3 px-2 font-medium">YES Price</th>
            <th className="text-left py-3 px-2 font-medium">NO Price</th>
            <th className="text-left py-3 px-2 font-medium">
              <SortBtn k="status" label="Status" />
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((market) => (
            <tr
              key={market.id}
              onClick={() => onRowClick?.(market)}
              className={`border-b border-gray-700/50 cursor-pointer transition-colors ${
                selectedId === market.id
                  ? "bg-blue-900/30"
                  : "hover:bg-gray-800"
              }`}
            >
              <td className="py-3 px-3 max-w-xs">
                <span className="text-gray-200 line-clamp-2">{market.question}</span>
              </td>
              <td className="py-3 px-2">
                <span className="bg-gray-700 text-gray-200 px-2 py-0.5 rounded text-xs font-mono">
                  {market.crypto}
                </span>
              </td>
              <td className="py-3 px-2 text-gray-300">
                {market.resolution_date ? formatDate(market.resolution_date) : "—"}
              </td>
              <td className="py-3 px-2 text-green-400 font-mono">—</td>
              <td className="py-3 px-2 text-red-400 font-mono">—</td>
              <td className="py-3 px-2">
                <span
                  className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                    market.status === "active"
                      ? "bg-green-900/50 text-green-400"
                      : "bg-gray-700 text-gray-400"
                  }`}
                >
                  {market.status}
                </span>
              </td>
            </tr>
          ))}
          {sorted.length === 0 && (
            <tr>
              <td colSpan={6} className="py-8 text-center text-gray-400">
                No markets found.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
