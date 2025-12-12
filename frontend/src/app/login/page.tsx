"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { clearTokens, login, storeTokens } from "@/lib/auth";

export default function LoginPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams?.get("next") || "/";
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const mutation = useMutation({
    mutationFn: () => login(username, password),
    onSuccess: (data) => {
      storeTokens(data);
      router.push(next);
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    mutation.mutate();
  };

  return (
    <main className="p-8 max-w-md mx-auto">
      <h1 className="text-2xl font-semibold mb-4">Login</h1>
      <p className="text-gray-600 mb-6">Sign in to access your workspace.</p>
      <form className="space-y-4" onSubmit={handleSubmit}>
        <div>
          <label className="block text-sm font-medium text-gray-700">Username</label>
          <input
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-gray-900"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700">Password</label>
          <input
            type="password"
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-gray-900"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </div>
        <button
          type="submit"
          className="w-full rounded-md bg-gray-900 text-white px-4 py-2 font-medium hover:bg-gray-800 disabled:opacity-60"
          disabled={mutation.isLoading}
        >
          {mutation.isLoading ? "Signing in..." : "Sign in"}
        </button>
        {mutation.isError && (
          <p className="text-sm text-red-600">Invalid credentials or server unavailable.</p>
        )}
        <button
          type="button"
          className="text-sm text-gray-600 underline"
          onClick={() => {
            clearTokens();
            setUsername("");
            setPassword("");
          }}
        >
          Clear tokens
        </button>
      </form>
    </main>
  );
}
