"use client";

import { useMemo } from "react";
import type { Position } from "@/lib/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function strategyLabel(strategy: string): string {
  switch (strategy) {
    case "volatility_spread":
      return "Vol Spread";
    case "stink_bid":
      return "Stink Bid";
    case "liquidity_sniper":
      return "Liq Sniper";
    default:
      return strategy;
  }
}

function formatAge(openedAt: string): string {
  const opened = new Date(openedAt).getTime();
  const now = Date.now();
  const diffMs = now - opened;
  const days = Math.floor(diffMs / 86_400_000);
  const hours = Math.floor((diffMs % 86_400_000) / 3_600_000);
  if (days > 0) return `${days}d ${hours}h`;
  const mins = Math.floor((diffMs % 3_600_000) / 60_000);
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

function formatPnl(pnl: number | null): string {
  if (pnl === null || pnl === undefined) return "—";
  const sign = pnl >= 0 ? "+" : "";
  return `${sign}$${pnl.toFixed(2)}`;
}

// ---------------------------------------------------------------------------
// PositionCard
// ---------------------------------------------------------------------------

interface PositionCardProps {
  position: Position;
  onClose?: (id: number) => void;
}

export default function PositionCard({ position, onClose }: PositionCardProps) {
  const pnlValue = position.unrealized_pnl ?? position.net_pnl;
  const isPositive = (pnlValue ?? 0) >= 0;
  const age = useMemo(() => formatAge(position.opened_at), [position.opened_at]);

  return (
    <article className="position-card">
      {/* Header */}
      <div className="position-card__header">
        <span className="badge badge-strategy">
          {strategyLabel(position.strategy)}
        </span>
        <span className={`badge ${position.side === "YES" ? "badge-yes" : "badge-no"}`}>
          {position.side}
        </span>
        <span className="position-card__age">{age}</span>
      </div>

      {/* Entry info */}
      <div className="position-card__row">
        <span className="label">Entry</span>
        <span className="value">{(position.entry_price * 100).toFixed(1)}¢</span>
      </div>

      <div className="position-card__row">
        <span className="label">Size</span>
        <span className="value">${position.size_usd.toFixed(0)}</span>
      </div>

      {/* P&L */}
      <div className="position-card__row">
        <span className="label">
          {position.status === "open" ? "Unreal. P&L" : "Net P&L"}
        </span>
        <span
          className="value"
          style={{ color: isPositive ? "#22c55e" : "#ef4444", fontWeight: 700 }}
        >
          {formatPnl(pnlValue)}
        </span>
      </div>

      {/* Close button (open positions only) */}
      {position.status === "open" && onClose && (
        <button
          className="close-btn"
          onClick={() => onClose(position.id)}
          aria-label={`Close position ${position.id}`}
        >
          Close
        </button>
      )}

      <style jsx>{`
        .position-card {
          background: #1e293b;
          border: 1px solid #334155;
          border-radius: 12px;
          padding: 14px;
          display: flex;
          flex-direction: column;
          gap: 8px;
          transition: border-color 0.15s;
        }
        .position-card:hover {
          border-color: #475569;
        }
        .position-card__header {
          display: flex;
          gap: 6px;
          align-items: center;
          flex-wrap: wrap;
        }
        .position-card__age {
          font-size: 0.7rem;
          color: #64748b;
          margin-left: auto;
        }
        .position-card__row {
          display: flex;
          justify-content: space-between;
          align-items: center;
        }
        .label {
          font-size: 0.75rem;
          color: #64748b;
        }
        .value {
          font-size: 0.875rem;
          color: #e2e8f0;
          font-weight: 500;
        }
        .badge {
          font-size: 0.65rem;
          font-weight: 600;
          padding: 2px 7px;
          border-radius: 999px;
          text-transform: uppercase;
          letter-spacing: 0.04em;
        }
        .badge-strategy { background: #6366f122; color: #818cf8; }
        .badge-yes { background: #22c55e22; color: #22c55e; }
        .badge-no { background: #ef444422; color: #ef4444; }
        .close-btn {
          margin-top: 4px;
          background: #ef444420;
          color: #ef4444;
          border: 1px solid #ef444440;
          border-radius: 6px;
          padding: 5px 12px;
          font-size: 0.75rem;
          font-weight: 600;
          cursor: pointer;
          transition: background 0.15s;
          width: 100%;
        }
        .close-btn:hover {
          background: #ef444440;
        }
      `}</style>
    </article>
  );
}
