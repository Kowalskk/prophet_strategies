"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import type { SystemStatus } from "@/lib/types";
import KillSwitch from "@/components/common/KillSwitch";

export default function StatusBar() {
  const { data: status, mutate } = useSWR<SystemStatus>("/status", fetcher, {
    refreshInterval: 15000,
  });

  const lastUpdate = new Date().toLocaleTimeString();

  return (
    <footer className="h-10 bg-gray-900 border-t border-gray-700 flex items-center justify-between px-6 text-xs text-gray-400">
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1.5">
          <div className={`w-2 h-2 rounded-full ${status ? "bg-green-400" : "bg-red-400"}`} />
          <span>{status ? "Connected" : "Disconnected"}</span>
        </div>
        <span>Last update: {lastUpdate}</span>
        <span className="text-yellow-400 font-medium">Paper Trading Mode</span>
      </div>
      <KillSwitch
        isActive={status?.kill_switch ?? false}
        onToggle={() => mutate()}
      />
    </footer>
  );
}
