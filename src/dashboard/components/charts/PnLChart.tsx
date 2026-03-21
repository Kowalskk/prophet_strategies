"use client";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import type { PnLPoint } from "@/lib/types";

interface PnLChartProps {
  data: PnLPoint[];
}

export default function PnLChart({ data }: PnLChartProps) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={data} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
        <XAxis dataKey="date" tick={{ fill: "#9CA3AF", fontSize: 11 }} tickLine={false} />
        <YAxis
          tick={{ fill: "#9CA3AF", fontSize: 11 }}
          tickLine={false}
          tickFormatter={(v: number) => `$${v.toFixed(0)}`}
        />
        <Tooltip
          contentStyle={{ backgroundColor: "#1F2937", border: "1px solid #374151", borderRadius: 6 }}
          labelStyle={{ color: "#F9FAFB" }}
          formatter={(value) => [`$${Number(value).toFixed(2)}`, "Cumulative P&L"]}
        />
        <Line
          type="monotone"
          dataKey="pnl"
          stroke="#3B82F6"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4, fill: "#3B82F6" }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
