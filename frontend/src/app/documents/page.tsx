"use client";

import DocumentTable from "@/components/DocumentTable";
import DocumentUpload from "@/components/DocumentUpload";
import AuthGate from "@/components/AuthGate";
import { useDocuments } from "@/lib/useDocuments";

export default function DocumentsPage() {
  const { data: documents = [], isLoading } = useDocuments();

  return (
    <AuthGate>
      <main className="p-8 space-y-6">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-gray-900">Documents</h1>
            <p className="text-gray-600 mt-1">Manage uploads, processing status, and exports.</p>
          </div>
        </header>

        <section className="grid gap-4 lg:grid-cols-3">
          <div className="lg:col-span-2">
            {isLoading ? (
              <div className="rounded-lg border border-gray-200 bg-white shadow-sm p-6 text-gray-600">
                Loading documents...
              </div>
            ) : (
              <DocumentTable documents={documents} />
            )}
          </div>
          <div className="rounded-lg border border-gray-200 bg-white shadow-sm p-4">
            <h2 className="text-lg font-semibold text-gray-900">Upload</h2>
            <p className="text-sm text-gray-500 mb-3">Drop a PDF/image to start processing.</p>
            <DocumentUpload />
          </div>
        </section>
      </main>
    </AuthGate>
  );
}
