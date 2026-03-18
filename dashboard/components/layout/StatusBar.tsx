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
    <footer className="h-12 glass border-t border-white/5 flex items-center justify-between px-8 text-xs text-slate-400 z-50">
      <div className="flex items-center gap-6">
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full shadow-lg ${status ? "bg-green-500 shadow-green-500/30" : "bg-red-500 shadow-red-500/30"}`} />
          <span className="font-semibold tracking-wide uppercase text-[10px]">{status ? "System Connected" : "System Offline"}</span>
        </div>
        <div className="h-4 w-[1px] bg-white/10" />
        <span className="font-medium">Update Sync: {lastUpdate}</span>
        <div className="h-4 w-[1px] bg-white/10" />
        <span className="text-gold font-bold tracking-widest uppercase text-[10px]">Paper Trading</span>
      </div>
      <div className="flex items-center gap-4">
        <KillSwitch
          isActive={status?.kill_switch ?? false}
          onToggle={() => mutate()}
        />
      </div>
    </footer>
  );
}
