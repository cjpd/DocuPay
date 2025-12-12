import { Document } from "@/lib/types";

export default function DocumentTable({ documents }: { documents: Document[] }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">All documents</h2>
          <p className="text-sm text-gray-500">Invoices, contracts, resumes, and more.</p>
        </div>
        <button className="text-sm text-gray-700 hover:text-gray-900">Export CSV</button>
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
            {documents.map((doc) => (
              <tr key={doc.id} className="hover:bg-gray-50">
                <td className="px-4 py-3 font-medium text-gray-900">{doc.id}</td>
                <td className="px-4 py-3 text-gray-700">{doc.doc_type || "—"}</td>
                <td className="px-4 py-3 text-gray-700 capitalize">{doc.status}</td>
                <td className="px-4 py-3 text-gray-700">{doc.confidence || "—"}</td>
                <td className="px-4 py-3 text-gray-500">
                  {doc.created_at ? new Date(doc.created_at).toLocaleString() : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
