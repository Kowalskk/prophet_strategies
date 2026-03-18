"use client";

import type { Market } from "@/lib/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function cryptoBadgeStyle(crypto: string): string {
  switch (crypto.toUpperCase()) {
    case "BTC":
      return "badge-btc";
    case "ETH":
      return "badge-eth";
    case "SOL":
      return "badge-sol";
    default:
      return "badge-default";
  }
}

function statusBadgeStyle(status: string): string {
  switch (status) {
    case "active":
      return "badge-active";
    case "resolved":
      return "badge-resolved";
    case "expired":
      return "badge-expired";
    default:
      return "badge-default";
  }
}

function formatResolutionDate(dateStr: string | null): string {
  if (!dateStr) return "—";
  const d = new Date(dateStr);
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function formatPrice(price: number | null): string {
  if (price === null || price === undefined) return "—";
  return `${(price * 100).toFixed(1)}¢`;
}

// ---------------------------------------------------------------------------
// MarketCard
// ---------------------------------------------------------------------------

interface MarketCardProps {
  market: Market;
  onSelect?: (market: Market) => void;
}

export default function MarketCard({ market, onSelect }: MarketCardProps) {
  return (
    <article
      className="market-card"
      onClick={() => onSelect?.(market)}
      style={{ cursor: onSelect ? "pointer" : "default" }}
      role={onSelect ? "button" : undefined}
      tabIndex={onSelect ? 0 : undefined}
      onKeyDown={(e) => e.key === "Enter" && onSelect?.(market)}
      aria-label={market.question}
    >
      {/* Header row */}
      <div className="market-card__header">
        <span className={`badge ${cryptoBadgeStyle(market.crypto)}`}>
          {market.crypto}
        </span>
        <span className={`badge ${statusBadgeStyle(market.status)}`}>
          {market.status}
        </span>
      </div>

      {/* Question */}
      <p className="market-card__question">{market.question}</p>

      {/* Footer */}
      <div className="market-card__footer">
        <span className="market-card__date">
          🗓 {formatResolutionDate(market.resolution_date)}
        </span>
        <div className="market-card__prices">
          <span className="price price--yes">
            YES {formatPrice(null)}
          </span>
          <span className="price price--no">
            NO {formatPrice(null)}
          </span>
        </div>
      </div>

      <style jsx>{`
        .market-card {
          background: #1e293b;
          border: 1px solid #334155;
          border-radius: 12px;
          padding: 16px;
          transition: border-color 0.15s, transform 0.1s;
          display: flex;
          flex-direction: column;
          gap: 10px;
        }
        .market-card:hover {
          border-color: #6366f1;
          transform: translateY(-1px);
        }
        .market-card__header {
          display: flex;
          gap: 8px;
          align-items: center;
        }
        .market-card__question {
          font-size: 0.875rem;
          color: #e2e8f0;
          line-height: 1.4;
          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
          overflow: hidden;
          margin: 0;
        }
        .market-card__footer {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-top: auto;
        }
        .market-card__date {
          font-size: 0.75rem;
          color: #64748b;
        }
        .market-card__prices {
          display: flex;
          gap: 8px;
        }
        .badge {
          font-size: 0.7rem;
          font-weight: 600;
          padding: 2px 8px;
          border-radius: 999px;
          text-transform: uppercase;
          letter-spacing: 0.04em;
        }
        .badge-btc { background: #f59e0b22; color: #f59e0b; }
        .badge-eth { background: #818cf822; color: #818cf8; }
        .badge-sol { background: #34d39922; color: #34d399; }
        .badge-active { background: #22c55e22; color: #22c55e; }
        .badge-resolved { background: #94a3b822; color: #94a3b8; }
        .badge-expired { background: #f59e0b22; color: #f59e0b; }
        .badge-default { background: #33415522; color: #94a3b8; }
        .price {
          font-size: 0.75rem;
          font-weight: 600;
          padding: 2px 6px;
          border-radius: 4px;
        }
        .price--yes { background: #22c55e22; color: #22c55e; }
        .price--no { background: #ef444422; color: #ef4444; }
      `}</style>
    </article>
  );
}
