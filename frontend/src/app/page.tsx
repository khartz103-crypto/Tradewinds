"use client";

import { useEffect, useState } from "react";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

export default function Home() {
  const [status, setStatus] = useState<"loading" | "ok" | "error">("loading");

  useEffect(() => {
    async function checkHealth() {
      try {
        const res = await fetch(`${BACKEND_URL}/api/health`);
        if (res.ok) {
          const data = await res.json();
          setStatus(data.status === "ok" ? "ok" : "error");
        } else {
          setStatus("error");
        }
      } catch {
        setStatus("error");
      }
    }
    checkHealth();
    const interval = setInterval(checkHealth, 30_000);
    return () => clearInterval(interval);
  }, []);

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 p-8">
      <h1 className="text-5xl font-bold tracking-tight">TradeWind AI</h1>
      <p className="text-lg text-gray-400">
        AI-powered swing trading platform
      </p>
      <div className="flex items-center gap-2 rounded-lg border border-gray-800 px-4 py-2">
        <span className="text-sm text-gray-500">Backend:</span>
        {status === "loading" && (
          <span className="text-sm text-yellow-400">Checking...</span>
        )}
        {status === "ok" && (
          <span className="flex items-center gap-1.5 text-sm text-green-400">
            <span className="inline-block h-2 w-2 rounded-full bg-green-400" />
            Connected
          </span>
        )}
        {status === "error" && (
          <span className="flex items-center gap-1.5 text-sm text-red-400">
            <span className="inline-block h-2 w-2 rounded-full bg-red-400" />
            Disconnected
          </span>
        )}
      </div>
    </main>
  );
}
