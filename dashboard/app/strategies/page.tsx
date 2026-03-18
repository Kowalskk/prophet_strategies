"use client";

import { useState } from "react";
import useSWR from "swr";
import { fetcher, api } from "@/lib/api";
import type { Strategy } from "@/lib/types";
import Header from "@/components/layout/Header";
import Loading from "@/components/common/Loading";

function StrategyCard({ strategy, onToggle }: { strategy: Strategy; onToggle: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [params, setParams] = useState(JSON.stringify(strategy.default_params, null, 2));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const handleToggle = async () => {
    setToggling(true);
    await api.toggleStrategy(strategy.name);
    setToggling(false);
    onToggle();
  };

  const handleSaveParams = async () => {
    setSaving(true);
    try {
      const parsed = JSON.parse(params) as Record<string, unknown>;
      await api.updateStrategyConfig(strategy.name, parsed);
      setSaved(true);
    } catch {
      alert("Invalid JSON");
    }
    setSaving(false);
  };

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <h3 className="text-white font-semibold font-mono">{strategy.name}</h3>
            <span
              className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                strategy.enabled
                  ? "bg-green-900/50 text-green-400"
                  : "bg-gray-700 text-gray-400"
              }`}
            >
              {strategy.enabled ? "Enabled" : "Disabled"}
            </span>
          </div>
          <p className="text-gray-400 text-sm mt-1">{strategy.description}</p>
        </div>
        <div className="flex gap-2 ml-4">
          <button
            onClick={() => setExpanded(!expanded)}
            className="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-gray-200 text-xs rounded"
          >
            {expanded ? "Collapse" : "Config"}
          </button>
          <button
            onClick={handleToggle}
            disabled={toggling}
            className={`px-3 py-1.5 text-xs rounded disabled:opacity-50 ${
              strategy.enabled
                ? "bg-red-800 hover:bg-red-700 text-red-200"
                : "bg-green-800 hover:bg-green-700 text-green-200"
            }`}
          >
            {toggling ? "..." : strategy.enabled ? "Disable" : "Enable"}
          </button>
        </div>
      </div>

      {expanded && (
        <div className="mt-4 space-y-3">
          <p className="text-gray-400 text-xs font-medium uppercase tracking-wide">Parameters (JSON)</p>
          <textarea
            value={params}
            onChange={(e) => { setParams(e.target.value); setSaved(false); }}
            className="w-full bg-gray-900 border border-gray-600 rounded p-3 text-gray-200 text-xs font-mono h-48 resize-none focus:outline-none focus:border-blue-500"
          />
          <button
            onClick={handleSaveParams}
            disabled={saving}
            className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs rounded disabled:opacity-50"
          >
            {saving ? "Saving..." : saved ? "Saved!" : "Save Parameters"}
          </button>
        </div>
      )}
    </div>
  );
}

export default function StrategiesPage() {
  const { data: strategies, isLoading, mutate } = useSWR<Strategy[]>("/strategies", fetcher, {
    refreshInterval: 30000,
  });

  return (
    <div className="flex flex-col flex-1">
      <Header title="Strategies" />
      <div className="p-6 space-y-4">
        <p className="text-gray-400 text-sm">
          Manage trading strategies — enable/disable and tune parameters.
        </p>
        {isLoading ? (
          <Loading />
        ) : (
          <div className="space-y-4">
            {(strategies ?? []).map((s) => (
              <StrategyCard key={s.name} strategy={s} onToggle={() => mutate()} />
            ))}
            {(strategies ?? []).length === 0 && (
              <div className="text-gray-400 text-sm py-8 text-center">No strategies available.</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
