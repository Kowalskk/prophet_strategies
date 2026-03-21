"use client";

import { useState } from "react";
import type { Strategy } from "@/lib/types";
import { api } from "@/lib/api";

interface StrategySelectorProps {
  marketId: number;
  strategies: Strategy[];
}

export default function StrategySelector({ marketId, strategies }: StrategySelectorProps) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const toggle = (name: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
    setSaved(false);
  };

  const handleSave = async () => {
    setSaving(true);
    for (const name of selected) {
      await api.assignStrategy(name, [marketId]);
    }
    setSaving(false);
    setSaved(true);
  };

  return (
    <div className="space-y-2">
      <p className="text-gray-400 text-xs font-medium uppercase tracking-wide">Assign Strategies</p>
      {strategies.map((strategy) => (
        <label key={strategy.name} className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={selected.has(strategy.name)}
            onChange={() => toggle(strategy.name)}
            className="w-4 h-4 rounded border-gray-600 bg-gray-700 text-blue-500"
          />
          <span className="text-gray-200 text-sm">{strategy.name}</span>
          {!strategy.enabled && (
            <span className="text-gray-500 text-xs">(disabled)</span>
          )}
        </label>
      ))}
      <button
        onClick={handleSave}
        disabled={saving || selected.size === 0}
        className="mt-2 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs rounded disabled:opacity-50"
      >
        {saving ? "Saving..." : saved ? "Saved!" : "Apply"}
      </button>
    </div>
  );
}
