"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import type { SystemStatus } from "@/lib/types";

interface HeaderProps {
  title: string;
}

export default function Header({ title }: HeaderProps) {
  const { data: status } = useSWR<SystemStatus>("/status", fetcher, { refreshInterval: 15000 });

  const isRunning = status && !status.kill_switch;

  return (
    <header className="h-14 bg-gray-900 border-b border-gray-700 flex items-center justify-between px-6">
      <h1 className="text-white font-semibold text-lg">{title}</h1>
      <div className="flex items-center gap-3">
        <span className="bg-yellow-500/20 text-yellow-400 text-xs font-bold px-3 py-1 rounded-full border border-yellow-500/50">
          PAPER TRADING
        </span>
        <div className="flex items-center gap-2">
          <div
            className={`w-2.5 h-2.5 rounded-full ${
              isRunning ? "bg-green-400" : "bg-red-400"
            }`}
          />
          <span className="text-gray-400 text-sm">
            {status?.kill_switch ? "Kill Switch Active" : isRunning ? "Running" : "Connecting..."}
          </span>
        </div>
      </div>
    </header>
  );
}
