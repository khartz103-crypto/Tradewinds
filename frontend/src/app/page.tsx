"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { getToken } from "@/lib/api";

export default function Home() {
  const router = useRouter();

  // Already signed in → go straight to the dashboard.
  useEffect(() => {
    if (getToken()) {
      router.replace("/dashboard");
    }
  }, [router]);

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 bg-gray-950 p-8 text-center">
      <h1 className="text-5xl font-bold tracking-tight text-gray-100">
        TradeWind AI
      </h1>
      <p className="text-lg text-gray-400">AI-Powered Swing Trading Platform</p>
      <Link
        href="/login"
        className="rounded-lg bg-blue-600 px-6 py-3 font-semibold text-white transition hover:bg-blue-500"
      >
        Launch Dashboard
      </Link>
    </main>
  );
}
