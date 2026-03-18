"use client";

import type { OrderBook } from "@/lib/types";
import { formatUSD } from "@/lib/utils";

interface OrderBookVizProps {
  orderbook: OrderBook | null;
}

export default function OrderBookViz({ orderbook }: OrderBookVizProps) {
  if (!orderbook) {
    return <div className="text-gray-400 text-sm p-4">No order book data available.</div>;
  }

  const midPrice =
    orderbook.best_bid && orderbook.best_ask
      ? (orderbook.best_bid + orderbook.best_ask) / 2
      : null;

  const topAsks = [...(orderbook.asks ?? [])].sort((a, b) => a.price - b.price).slice(0, 8);
  const topBids = [...(orderbook.bids ?? [])].sort((a, b) => b.price - a.price).slice(0, 8);

  const maxSize = Math.max(
    ...topBids.map((b) => b.size),
    ...topAsks.map((a) => a.size),
    1
  );

  return (
    <div className="bg-gray-900 rounded-lg p-3 text-xs font-mono">
      <div className="grid grid-cols-3 text-gray-500 mb-1 px-1">
        <span>Price</span>
        <span className="text-right">Size</span>
        <span className="text-right">Depth</span>
      </div>

      {/* Asks (lowest first) */}
      {[...topAsks].reverse().map((level, i) => (
        <div key={`ask-${i}`} className="grid grid-cols-3 items-center py-0.5 px-1 relative">
          <div
            className="absolute inset-0 bg-red-900/20 rounded"
            style={{ width: `${(level.size / maxSize) * 100}%` }}
          />
          <span className="text-red-400 relative">{level.price.toFixed(3)}</span>
          <span className="text-gray-300 text-right relative">{level.size.toFixed(1)}</span>
          <span className="text-gray-500 text-right relative">{formatUSD(level.size)}</span>
        </div>
      ))}

      {/* Mid price */}
      {midPrice && (
        <div className="border-t border-b border-gray-600 my-1 py-1 px-1 text-center text-blue-400 font-bold">
          Mid: {midPrice.toFixed(3)}
          {orderbook.spread_pct != null && (
            <span className="text-gray-400 font-normal ml-2">
              Spread: {(orderbook.spread_pct * 100).toFixed(2)}%
            </span>
          )}
        </div>
      )}

      {/* Bids */}
      {topBids.map((level, i) => (
        <div key={`bid-${i}`} className="grid grid-cols-3 items-center py-0.5 px-1 relative">
          <div
            className="absolute inset-0 bg-green-900/20 rounded"
            style={{ width: `${(level.size / maxSize) * 100}%` }}
          />
          <span className="text-green-400 relative">{level.price.toFixed(3)}</span>
          <span className="text-gray-300 text-right relative">{level.size.toFixed(1)}</span>
          <span className="text-gray-500 text-right relative">{formatUSD(level.size)}</span>
        </div>
      ))}
    </div>
  );
}
