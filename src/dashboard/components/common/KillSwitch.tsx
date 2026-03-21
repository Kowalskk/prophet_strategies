"use client";

import { useState } from "react";
import { api } from "@/lib/api";

interface KillSwitchProps {
  isActive: boolean;
  onToggle?: () => void;
}

export default function KillSwitch({ isActive, onToggle }: KillSwitchProps) {
  const [showConfirm, setShowConfirm] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleConfirm = async () => {
    setLoading(true);
    await api.killSwitch();
    setLoading(false);
    setShowConfirm(false);
    onToggle?.();
  };

  return (
    <>
      <button
        onClick={() => setShowConfirm(true)}
        className={`px-4 py-2 rounded-lg font-bold text-sm transition-colors ${
          isActive
            ? "bg-red-700 hover:bg-red-600 text-white border border-red-500"
            : "bg-green-700 hover:bg-green-600 text-white border border-green-500"
        }`}
      >
        {isActive ? "KILL SWITCH: ON" : "KILL SWITCH: OFF"}
      </button>

      {showConfirm && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-gray-800 border border-gray-600 rounded-lg p-6 max-w-sm w-full mx-4">
            <h3 className="text-white font-bold text-lg mb-2">Confirm Kill Switch</h3>
            <p className="text-gray-300 text-sm mb-4">
              {isActive
                ? "Resume trading? The engine will start accepting new signals."
                : "Stop all trading? No new orders will be placed until you re-enable."}
            </p>
            <div className="flex gap-3">
              <button
                onClick={handleConfirm}
                disabled={loading}
                className="flex-1 bg-red-600 hover:bg-red-500 text-white py-2 rounded-lg font-medium text-sm disabled:opacity-50"
              >
                {loading ? "Toggling..." : "Confirm"}
              </button>
              <button
                onClick={() => setShowConfirm(false)}
                className="flex-1 bg-gray-700 hover:bg-gray-600 text-white py-2 rounded-lg font-medium text-sm"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
