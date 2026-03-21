"use client";

import { useState, useEffect } from "react";
import useSWR from "swr";
import { fetcher, api } from "@/lib/api";
import type { Config, RiskMetrics } from "@/lib/types";
import Header from "@/components/layout/Header";
import Loading from "@/components/common/Loading";

export default function SettingsPage() {
  const { data: config, isLoading, mutate } = useSWR<Config>("/config", fetcher);
  const { data: risk } = useSWR<RiskMetrics>("/config/risk", fetcher, { refreshInterval: 15000 });
  const [form, setForm] = useState<Partial<Config>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [showModeDialog, setShowModeDialog] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);

  useEffect(() => {
    if (config) setForm(config);
  }, [config]);

  const handleSave = async () => {
    setSaving(true);
    await api.updateConfig(form);
    setSaving(false);
    setSaved(true);
    mutate();
  };

  const handleTestConnection = async () => {
    setTesting(true);
    setTestResult(null);
    const result = await api.health();
    setTestResult(result ? `Connected — v${result.version}, uptime ${result.uptime_seconds.toFixed(0)}s` : "Connection failed");
    setTesting(false);
  };

  const numberField = (label: string, key: keyof Config, step = 1) => (
    <div>
      <label className="block text-gray-400 text-sm mb-1">{label}</label>
      <input
        type="number"
        step={step}
        value={(form[key] as number) ?? ""}
        onChange={(e) => {
          setForm((f) => ({ ...f, [key]: parseFloat(e.target.value) }));
          setSaved(false);
        }}
        className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-gray-200 text-sm focus:outline-none focus:border-blue-500"
      />
    </div>
  );

  return (
    <div className="flex flex-col flex-1">
      <Header title="Settings" />
      <div className="p-6 space-y-6 max-w-2xl">
        {isLoading ? (
          <Loading />
        ) : (
          <>
            {/* Risk Limits */}
            <div className="bg-gray-800 rounded-lg p-4 border border-gray-700 space-y-4">
              <h2 className="text-white font-semibold">Risk Limits</h2>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {numberField("Max Position Per Market ($)", "max_position_per_market")}
                {numberField("Max Daily Loss ($)", "max_daily_loss")}
                {numberField("Max Open Positions", "max_open_positions")}
                {numberField("Max Concentration (%)", "max_concentration", 0.01)}
                {numberField("Max Drawdown (%)", "max_drawdown_total", 0.01)}
              </div>
              <button
                onClick={handleSave}
                disabled={saving}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded disabled:opacity-50"
              >
                {saving ? "Saving..." : saved ? "Saved!" : "Save Risk Limits"}
              </button>
            </div>

            {/* Risk Utilization */}
            {risk && (
              <div className="bg-gray-800 rounded-lg p-4 border border-gray-700 space-y-3">
                <h2 className="text-white font-semibold">Current Risk Utilization</h2>
                <RiskBar label="Daily Loss" pct={risk.daily_loss_pct} />
                <RiskBar label="Open Positions" pct={risk.open_positions_pct} />
                <RiskBar label="Drawdown" pct={risk.drawdown_pct} />
              </div>
            )}

            {/* Trading Mode */}
            <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
              <h2 className="text-white font-semibold mb-3">Trading Mode</h2>
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-gray-200 text-sm font-medium">
                    Current mode:{" "}
                    <span className="text-yellow-400 font-bold">
                      {config?.paper_trading ? "Paper Trading" : "LIVE Trading"}
                    </span>
                  </p>
                  <p className="text-gray-400 text-xs mt-1">
                    {config?.paper_trading
                      ? "All trades are simulated. No real funds at risk."
                      : "WARNING: Real funds are at risk!"}
                  </p>
                </div>
                <button
                  onClick={() => setShowModeDialog(true)}
                  className="px-3 py-1.5 bg-yellow-700 hover:bg-yellow-600 text-yellow-100 text-xs rounded"
                >
                  Toggle Mode
                </button>
              </div>
            </div>

            {/* API Test */}
            <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
              <h2 className="text-white font-semibold mb-3">API Connection</h2>
              <div className="flex items-center gap-3">
                <button
                  onClick={handleTestConnection}
                  disabled={testing}
                  className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-gray-200 text-sm rounded disabled:opacity-50"
                >
                  {testing ? "Testing..." : "Test Connection"}
                </button>
                {testResult && (
                  <span className={`text-sm ${testResult.startsWith("Connected") ? "text-green-400" : "text-red-400"}`}>
                    {testResult}
                  </span>
                )}
              </div>
              <p className="text-gray-500 text-xs mt-2">
                API URL: {process.env.NEXT_PUBLIC_API_URL ?? "(not set)"}
              </p>
            </div>
          </>
        )}
      </div>

      {/* Mode toggle confirmation */}
      {showModeDialog && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-gray-800 border border-red-700 rounded-lg p-6 max-w-sm w-full mx-4">
            <h3 className="text-red-400 font-bold text-lg mb-2">WARNING</h3>
            <p className="text-gray-200 text-sm mb-4">
              {config?.paper_trading
                ? "Switching to LIVE mode will place REAL orders with REAL funds. Are you absolutely sure?"
                : "Switch back to Paper Trading mode?"}
            </p>
            <div className="flex gap-3">
              <button
                onClick={async () => {
                  await api.updateConfig({ paper_trading: !config?.paper_trading });
                  mutate();
                  setShowModeDialog(false);
                }}
                className="flex-1 bg-red-600 hover:bg-red-500 text-white py-2 rounded-lg font-medium text-sm"
              >
                Confirm
              </button>
              <button
                onClick={() => setShowModeDialog(false)}
                className="flex-1 bg-gray-700 hover:bg-gray-600 text-white py-2 rounded-lg font-medium text-sm"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function RiskBar({ label, pct }: { label: string; pct: number }) {
  const pctDisplay = Math.min(pct * 100, 100);
  const color = pctDisplay > 80 ? "bg-red-500" : pctDisplay > 60 ? "bg-yellow-500" : "bg-green-500";
  return (
    <div>
      <div className="flex justify-between text-xs text-gray-400 mb-1">
        <span>{label}</span>
        <span>{pctDisplay.toFixed(1)}%</span>
      </div>
      <div className="w-full bg-gray-700 rounded-full h-2">
        <div className={`${color} h-2 rounded-full`} style={{ width: `${pctDisplay}%` }} />
      </div>
    </div>
  );
}
