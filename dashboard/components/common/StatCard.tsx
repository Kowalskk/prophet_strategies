import type { LucideIcon } from "lucide-react";
import { getPnLColor } from "@/lib/utils";

interface StatCardProps {
  title: string;
  value: string;
  subtitle?: string;
  trend?: number;
  icon?: LucideIcon;
}

export default function StatCard({ title, value, subtitle, trend, icon: Icon }: StatCardProps) {
  return (
    <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <p className="text-gray-400 text-sm font-medium">{title}</p>
          <p className="text-white text-2xl font-bold mt-1">{value}</p>
          {subtitle && <p className="text-gray-400 text-xs mt-1">{subtitle}</p>}
          {trend !== undefined && (
            <p className={`text-sm mt-1 font-medium ${getPnLColor(trend)}`}>
              {trend >= 0 ? "▲" : "▼"} {Math.abs(trend).toFixed(2)}%
            </p>
          )}
        </div>
        {Icon && (
          <div className="bg-gray-700 rounded-lg p-2 ml-3">
            <Icon className="h-5 w-5 text-gray-300" />
          </div>
        )}
      </div>
    </div>
  );
}
