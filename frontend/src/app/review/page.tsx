"use client";

import AuthGate from "@/components/AuthGate";
import { useReviewQueue } from "@/lib/useReviewQueue";

export default function ReviewQueuePage() {
  const { data: queue = [], isLoading } = useReviewQueue();

  return (
    <AuthGate>
      <main className="p-8 space-y-6">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-gray-900">Review queue</h1>
            <p className="text-gray-600 mt-1">Low-confidence documents needing human check.</p>
          </div>
          <a
            href="/documents"
            className="rounded-md border border-gray-200 px-3 py-1.5 text-sm font-medium text-gray-800 hover:bg-gray-50"
          >
            Back to documents
          </a>
        </header>

        <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Items</h2>
              <p className="text-sm text-gray-500">Sort by priority and handle the oldest first.</p>
            </div>
          </div>
          {isLoading ? (
            <div className="p-4 text-gray-600">Loading review tasks...</div>
          ) : (
            <ul className="divide-y divide-gray-100">
              {queue.map((item) => (
                <li key={item.id} className="px-4 py-3 flex items-center justify-between hover:bg-gray-50">
                  <div>
                  <p className="font-medium text-gray-900">#{item.id}</p>
                  <p className="text-sm text-gray-500">
                    Status: {item.status} • Document: {typeof item.document === "object" ? item.document.id : item.document}
                  </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <a
                      href={`/review/${item.id}`}
                      className="text-sm text-gray-700 hover:text-gray-900 underline"
                    >
                      Review
                    </a>
                  </div>
                </li>
              ))}
              {queue.length === 0 && (
                <li className="px-4 py-3 text-sm text-gray-500">No review tasks pending.</li>
              )}
            </ul>
          )}
        </div>
      </main>
    </AuthGate>
  );
}
