"use client";

import {
  Cell,
  Label,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface WinRateChartProps {
  wins: number;
  losses: number;
  total: number;
}

// ---------------------------------------------------------------------------
// Custom label rendered in the centre of the donut
// ---------------------------------------------------------------------------

function CentreLabel({
  cx,
  cy,
  winRate,
}: {
  cx: number;
  cy: number;
  winRate: number;
}) {
  return (
    <text
      x={cx}
      y={cy}
      textAnchor="middle"
      dominantBaseline="central"
      className="win-rate-centre"
    >
      <tspan
        x={cx}
        dy="-0.3em"
        style={{ fontSize: "1.6rem", fontWeight: 700, fill: "#f1f5f9" }}
      >
        {Math.round(winRate)}%
      </tspan>
      <tspan
        x={cx}
        dy="1.6em"
        style={{ fontSize: "0.75rem", fontWeight: 400, fill: "#94a3b8" }}
      >
        Win Rate
      </tspan>
    </text>
  );
}

// ---------------------------------------------------------------------------
// WinRateChart
// ---------------------------------------------------------------------------

export default function WinRateChart({ wins, losses, total }: WinRateChartProps) {
  const winRate = total > 0 ? (wins / total) * 100 : 0;

  const data =
    total === 0
      ? [{ name: "No Trades", value: 1 }]
      : [
          { name: `Wins (${wins})`, value: wins },
          { name: `Losses (${losses})`, value: losses },
        ];

  const COLORS =
    total === 0 ? ["#334155"] : ["#22c55e", "#ef4444"];

  return (
    <ResponsiveContainer width="100%" height={220}>
      <PieChart>
        <Pie
          data={data}
          cx="50%"
          cy="50%"
          innerRadius={65}
          outerRadius={90}
          paddingAngle={total === 0 ? 0 : 3}
          dataKey="value"
          strokeWidth={0}
        >
          {data.map((_, index) => (
            <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
          ))}
          <Label
            content={({ viewBox }) => {
              const { cx, cy } = viewBox as { cx: number; cy: number };
              return <CentreLabel cx={cx} cy={cy} winRate={winRate} />;
            }}
          />
        </Pie>

        {total > 0 && (
          <Tooltip
            formatter={(value: any, name: any) => [value, name]}
            contentStyle={{
              backgroundColor: "#1e293b",
              border: "1px solid #334155",
              borderRadius: 8,
              color: "#f1f5f9",
            }}
          />
        )}

        {total > 0 && (
          <Legend
            iconType="circle"
            iconSize={8}
            formatter={(value) => (
              <span style={{ color: "#94a3b8", fontSize: "0.75rem" }}>
                {value}
              </span>
            )}
          />
        )}
      </PieChart>
    </ResponsiveContainer>
  );
}
