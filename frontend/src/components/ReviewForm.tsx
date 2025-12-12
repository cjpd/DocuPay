type Props = {
  fields: {
    key: string;
    label: string;
    value: string | number | null | undefined;
    confidence?: number;
  }[];
  lineItems?: any[];
  onChange: (key: string, value: string) => void;
  onApprove: () => void;
  onReject: () => void;
  isApproving?: boolean;
  isRejecting?: boolean;
};

export default function ReviewForm({
  fields,
  lineItems = [],
  onChange,
  onApprove,
  onReject,
  isApproving,
  isRejecting,
}: Props) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white shadow-sm p-4 space-y-3">
      <h2 className="text-lg font-semibold text-gray-900">Flagged fields</h2>
      <div className="space-y-2 text-sm">
        {fields.map((field) => (
          <label key={field.key} className="block">
            <span className="text-gray-700 flex items-center justify-between">
              {field.label}
              {field.confidence !== undefined && (
                <span className="text-xs font-semibold text-gray-600">{Math.round(field.confidence * 100)}%</span>
              )}
            </span>
            <input
              className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2"
              value={field.value ?? ""}
              onChange={(e) => onChange(field.key, e.target.value)}
            />
          </label>
        ))}
      </div>

      {lineItems.length > 0 && (
        <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="px-4 py-3 border-b border-gray-100">
            <h3 className="text-sm font-semibold text-gray-900">Line Items</h3>
          </div>
          <div className="divide-y divide-gray-100">
            {lineItems.map((item: any, idx: number) => {
              const conf = item.confidence ?? 0;
              const level =
                conf >= 0.95 ? "bg-green-50" : conf >= 0.8 ? "bg-amber-50" : conf ? "bg-red-50" : "bg-white";
              return (
                <div key={idx} className={`px-4 py-3 text-sm ${level}`}>
                  <div className="font-medium">{item.description}</div>
                  <div className="text-gray-600">
                    Qty: {item.quantity} @ {item.unit_price} • Conf: {conf ? `${Math.round(conf * 100)}%` : "—"}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="flex gap-2 pt-2">
        <button
          className="rounded-md bg-gray-900 text-white px-4 py-2 text-sm font-medium hover:bg-gray-800 disabled:opacity-60"
          onClick={onApprove}
          disabled={isApproving || isRejecting}
        >
          {isApproving ? "Approving..." : "Approve"}
        </button>
        <button
          className="rounded-md border border-gray-200 px-4 py-2 text-sm font-medium text-gray-800 hover:bg-gray-50 disabled:opacity-60"
          onClick={onReject}
          disabled={isApproving || isRejecting}
        >
          {isRejecting ? "Rejecting..." : "Request changes"}
        </button>
      </div>
    </div>
  );
}
