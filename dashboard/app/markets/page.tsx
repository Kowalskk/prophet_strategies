"use client";

import { useState } from "react";
import useSWR from "swr";
import { fetcher, api } from "@/lib/api";
import type { MarketList, Market, Strategy, OrderBook } from "@/lib/types";
import { usePrices } from "@/hooks/usePrices";
import Header from "@/components/layout/Header";
import MarketTable from "@/components/markets/MarketTable";
import StrategySelector from "@/components/markets/StrategySelector";
import OrderBookViz from "@/components/charts/OrderBookViz";
import Loading from "@/components/common/Loading";

const cryptos = ["All", "BTC", "ETH", "SOL"];

export default function MarketsPage() {
  const [crypto, setCrypto] = useState("All");
  const [selected, setSelected] = useState<Market | null>(null);

  const { data: marketList, isLoading } = useSWR<MarketList>(
    `/markets${crypto !== "All" ? `?crypto=${crypto}` : ""}`,
    fetcher,
    { refreshInterval: 30000 }
  );
  const { data: strategies } = useSWR<Strategy[]>("/strategies", fetcher);
  const { data: prices } = usePrices(30000);
  const { data: orderbook } = useSWR<OrderBook>(
    selected ? `/markets/${selected.id}/orderbook` : null,
    () => api.marketOrderBook(selected!.id) as Promise<OrderBook>,
    { refreshInterval: 10000 }
  );

  return (
    <div className="flex flex-col flex-1">
      <Header title="Markets" />
      <div className="p-6 space-y-4">
        {/* Filter buttons */}
        <div className="flex gap-2">
          {cryptos.map((c) => (
            <button
              key={c}
              onClick={() => setCrypto(c)}
              className={`px-4 py-1.5 rounded-full text-sm font-medium transition-colors ${
                crypto === c
                  ? "bg-blue-600 text-white"
                  : "bg-gray-800 text-gray-400 hover:text-white border border-gray-700"
              }`}
            >
              {c}
            </button>
          ))}
          <span className="ml-auto text-gray-400 text-sm self-center">
            {marketList?.total ?? 0} markets
          </span>
        </div>

        {/* Table */}
        <div className="bg-gray-800 rounded-lg border border-gray-700">
          {isLoading ? (
            <Loading />
          ) : (
            <MarketTable
              markets={marketList?.items ?? []}
              prices={prices ?? undefined}
              onRowClick={setSelected}
              selectedId={selected?.id}
            />
          )}
        </div>

        {/* Expanded detail */}
        {selected && (
          <div className="bg-gray-800 rounded-lg border border-gray-700 p-4 grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div>
              <h3 className="text-white font-semibold mb-2 text-sm">{selected.question}</h3>
              <OrderBookViz orderbook={orderbook ?? null} />
            </div>
            <div>
              <StrategySelector
                marketId={selected.id}
                strategies={strategies ?? []}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
