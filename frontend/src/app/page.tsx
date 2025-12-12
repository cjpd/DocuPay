"use client";

import AuthGate from "@/components/AuthGate";
import DocumentUpload from "@/components/DocumentUpload";
import { useDocuments } from "@/lib/useDocuments";
import { useReviewQueue } from "@/lib/useReviewQueue";
import { Document } from "@/lib/types";

const summaryCards = [
  { title: "Docs processed (24h)", value: "128", trend: "+12% vs yesterday" },
  { title: "Pending review", value: "9", trend: "3 high-priority" },
  { title: "Avg confidence", value: "91%", trend: "Goal: 95%" },
  { title: "Webhook success", value: "97%", trend: "Last hour" },
];

export default function HomePage() {
  const { data: documents } = useDocuments();
  const { data: reviewQueue } = useReviewQueue();

  const docsArray: Document[] = Array.isArray(documents) ? documents : [];
  const queueArray = Array.isArray(reviewQueue) ? reviewQueue : [];

  const recentDocuments = docsArray.slice(0, 4).map((doc) => ({
    id: doc.id,
    type: doc.doc_type || "—",
    status: doc.status,
    confidence: doc.confidence || "—",
    received: doc.created_at ? new Date(doc.created_at).toLocaleString() : "—",
  }));

  return (
    <AuthGate>
      <main className="p-8 space-y-6">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-gray-900">Dashboard</h1>
            <p className="text-gray-600 mt-1">AI-powered document processing overview.</p>
          </div>
          <a
            href="/documents"
            className="rounded-md bg-gray-900 text-white px-4 py-2 text-sm font-medium shadow hover:bg-gray-800"
          >
            Upload documents
          </a>
        </header>

        <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {summaryCards.map((card) => (
            <div key={card.title} className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
              <p className="text-sm text-gray-500">{card.title}</p>
              <p className="mt-2 text-2xl font-semibold text-gray-900">{card.value}</p>
              <p className="text-sm text-emerald-600 mt-1">{card.trend}</p>
            </div>
          ))}
        </section>

        <section className="grid gap-4 lg:grid-cols-3">
          <div className="lg:col-span-2 rounded-lg border border-gray-200 bg-white shadow-sm">
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
              <div>
                <h2 className="text-lg font-semibold text-gray-900">Recent documents</h2>
                <p className="text-sm text-gray-500">Latest uploads with status and confidence.</p>
              </div>
              <a className="text-sm text-gray-700 hover:text-gray-900" href="/documents">
                View all
              </a>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 text-left text-gray-500">
                  <tr>
                    <th className="px-4 py-3 font-medium">ID</th>
                    <th className="px-4 py-3 font-medium">Type</th>
                    <th className="px-4 py-3 font-medium">Status</th>
                    <th className="px-4 py-3 font-medium">Confidence</th>
                    <th className="px-4 py-3 font-medium">Received</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {recentDocuments.map((doc) => (
                    <tr key={doc.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3 font-medium text-gray-900">{doc.id}</td>
                      <td className="px-4 py-3 text-gray-700">{doc.type}</td>
                      <td className="px-4 py-3 text-gray-700">{doc.status}</td>
                      <td className="px-4 py-3 text-gray-700">{doc.confidence}</td>
                      <td className="px-4 py-3 text-gray-500">{doc.received}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
              <div>
                <h2 className="text-lg font-semibold text-gray-900">Review queue</h2>
                <p className="text-sm text-gray-500">Low-confidence items to review.</p>
              </div>
              <a className="text-sm text-gray-700 hover:text-gray-900" href="/review">
                Open queue
              </a>
            </div>
            <ul className="divide-y divide-gray-100">
              {queueArray.slice(0, 5).map((item) => (
                <li key={item.id} className="px-4 py-3">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="font-medium text-gray-900">#{item.id}</p>
                      <p className="text-sm text-gray-500">{item.status}</p>
                    </div>
                    <div className="text-right">
                      <span className="inline-flex items-center rounded-full px-2 py-1 text-xs font-medium bg-amber-100 text-amber-700">
                        Pending
                      </span>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-lg border border-gray-200 bg-white shadow-sm p-4">
            <h2 className="text-lg font-semibold text-gray-900">Quick upload</h2>
            <p className="text-sm text-gray-500 mb-3">Upload a document to kick off processing.</p>
            <DocumentUpload />
          </div>
          <div className="rounded-lg border border-gray-200 bg-white shadow-sm p-4">
            <h2 className="text-lg font-semibold text-gray-900">Tips</h2>
            <ul className="mt-2 space-y-2 text-sm text-gray-600">
              <li>- High-res scans improve OCR accuracy.</li>
              <li>- Configure webhooks to sync results to your system.</li>
              <li>- Low-confidence items are routed to the review queue.</li>
            </ul>
          </div>
        </section>
      </main>
    </AuthGate>
  );
}
