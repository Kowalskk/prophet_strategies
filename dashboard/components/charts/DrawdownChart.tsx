"use client";

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import type { PnLPoint } from "@/lib/types";

interface DrawdownChartProps {
  data: PnLPoint[];
}

export default function DrawdownChart({ data }: DrawdownChartProps) {
  // Calculate drawdown from cumulative P&L
  let peak = 0;
  const drawdownData = data.map((point) => {
    if (point.pnl > peak) peak = point.pnl;
    const drawdown = peak > 0 ? ((point.pnl - peak) / peak) * 100 : 0;
    return { date: point.date, drawdown };
  });

  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={drawdownData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
        <XAxis dataKey="date" tick={{ fill: "#9CA3AF", fontSize: 11 }} tickLine={false} />
        <YAxis
          tick={{ fill: "#9CA3AF", fontSize: 11 }}
          tickLine={false}
          tickFormatter={(v: number) => `${v.toFixed(1)}%`}
        />
        <Tooltip
          contentStyle={{ backgroundColor: "#1F2937", border: "1px solid #374151", borderRadius: 6 }}
          labelStyle={{ color: "#F9FAFB" }}
          formatter={(value) => [`${Number(value).toFixed(2)}%`, "Drawdown"]}
        />
        <Area
          type="monotone"
          dataKey="drawdown"
          stroke="#EF4444"
          fill="#EF444420"
          strokeWidth={2}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
