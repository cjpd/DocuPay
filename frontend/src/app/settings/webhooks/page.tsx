import AuthGate from "@/components/AuthGate";

const webhooks = [
  { id: 1, url: "https://example.com/webhook", status: "Active", secret: "••••••••" },
  { id: 2, url: "https://zapier.com/hooks/abc", status: "Inactive", secret: "••••••••" },
];

export default function WebhookSettingsPage() {
  return (
    <AuthGate>
      <main className="p-8 space-y-6">
        <header>
          <h1 className="text-2xl font-semibold text-gray-900">Webhook settings</h1>
          <p className="text-gray-600 mt-1">Configure delivery endpoints for completed extractions.</p>
        </header>

        <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Endpoints</h2>
              <p className="text-sm text-gray-500">Each organization can have multiple targets.</p>
            </div>
            <button className="text-sm rounded-md bg-gray-900 text-white px-3 py-1.5 font-medium hover:bg-gray-800">
              Add endpoint
            </button>
          </div>
          <ul className="divide-y divide-gray-100">
            {webhooks.map((wh) => (
              <li key={wh.id} className="px-4 py-3 flex items-center justify-between hover:bg-gray-50">
                <div>
                  <p className="font-medium text-gray-900">{wh.url}</p>
                  <p className="text-sm text-gray-500">Secret: {wh.secret}</p>
                </div>
                <div className="flex items-center gap-3">
                  <span
                    className={`inline-flex items-center rounded-full px-2 py-1 text-xs font-medium ${
                      wh.status === "Active" ? "bg-emerald-100 text-emerald-700" : "bg-gray-100 text-gray-700"
                    }`}
                  >
                    {wh.status}
                  </span>
                  <button className="text-sm text-gray-700 hover:text-gray-900">Edit</button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </main>
    </AuthGate>
  );
}
