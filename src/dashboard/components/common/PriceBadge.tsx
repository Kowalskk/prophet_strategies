interface PriceBadgeProps {
  bid: number | null;
  ask: number | null;
  side?: "YES" | "NO";
}

/** Shows bid / ask as cents (e.g. 32¢ / 35¢). Greys out when unavailable. */
export default function PriceBadge({ bid, ask, side }: PriceBadgeProps) {
  if (bid == null && ask == null) {
    return <span className="text-slate-600 text-xs font-mono">—</span>;
  }

  const bidColor = side === "NO" ? "text-red-400" : "text-green-400";
  const askColor = side === "NO" ? "text-red-300" : "text-green-300";

  return (
    <span className="inline-flex items-center gap-1 font-mono text-xs">
      <span className={bidColor}>{bid != null ? `${(bid * 100).toFixed(1)}¢` : "—"}</span>
      <span className="text-slate-600">/</span>
      <span className={askColor}>{ask != null ? `${(ask * 100).toFixed(1)}¢` : "—"}</span>
    </span>
  );
}
