"use client";

import AuthGate from "@/components/AuthGate";
import ReviewForm from "@/components/ReviewForm";
import PdfPreview from "@/components/PdfPreview";
import { useParams } from "next/navigation";
import { useReviewActions, useReviewTask } from "@/lib/useReviewTask";
import { useState } from "react";

export default function ReviewDetailPage() {
  const params = useParams();
  const id = params?.id as string;
  const { data: task, isLoading } = useReviewTask(id);
  const { approve, reject } = useReviewActions(id);
  const [corrections, setCorrections] = useState<Record<string, string>>({});

  const extracted = (task as any)?.document?.extracted_data;
  const fields = [
    { key: "invoice_number", label: "Invoice Number" },
    { key: "vendor_name", label: "Vendor" },
    { key: "invoice_date", label: "Invoice Date" },
    { key: "due_date", label: "Due Date" },
    { key: "total_amount", label: "Total Amount" },
    { key: "currency", label: "Currency" },
  ];

  return (
    <AuthGate>
      <main className="p-8 space-y-4">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-gray-900">Review: #{id}</h1>
            <p className="text-gray-600 mt-1">Check flagged fields and approve.</p>
          </div>
          <a
            href="/review"
            className="rounded-md border border-gray-200 px-3 py-1.5 text-sm font-medium text-gray-800 hover:bg-gray-50"
          >
            Back to queue
          </a>
        </header>

        {isLoading ? (
          <div className="rounded-lg border border-gray-200 bg-white shadow-sm p-4 text-gray-600">
            Loading review task...
          </div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            <PdfPreview />
            <div className="space-y-3">
              <ReviewForm
                fields={fields.map((f) => ({
                  ...f,
                  value: corrections[f.key] ?? extracted?.[f.key],
                  confidence: extracted?.field_confidences?.[f.key],
                }))}
                lineItems={extracted?.line_items || []}
                onChange={(key, val) => setCorrections((prev) => ({ ...prev, [key]: val }))}
                onApprove={() => approve.mutate(corrections)}
                onReject={() => reject.mutate()}
                isApproving={approve.isPending}
                isRejecting={reject.isPending}
              />

              <div className="rounded-lg border border-gray-200 bg-white shadow-sm p-4 space-y-2">
                <h3 className="text-sm font-semibold text-gray-900">OCR Text</h3>
                <p className="text-xs text-gray-600 whitespace-pre-wrap">
                  {task.document?.ocr_text || "No OCR text available."}
                </p>
              </div>

              <div className="rounded-lg border border-gray-200 bg-white shadow-sm p-4 space-y-2">
                <h3 className="text-sm font-semibold text-gray-900">Raw Extraction</h3>
                <pre className="text-xs text-gray-600 bg-gray-50 rounded p-2 overflow-auto max-h-48">
                  {JSON.stringify(extracted?.raw_extraction || {}, null, 2)}
                </pre>
              </div>
            </div>
          </div>
        )}
      </main>
    </AuthGate>
  );
}
