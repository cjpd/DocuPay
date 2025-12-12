type Props = {
  onApprove: () => void;
  onReject: () => void;
  isApproving?: boolean;
  isRejecting?: boolean;
};

export default function ReviewForm({ onApprove, onReject, isApproving, isRejecting }: Props) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white shadow-sm p-4 space-y-3">
      <h2 className="text-lg font-semibold text-gray-900">Flagged fields</h2>
      <div className="space-y-2 text-sm">
        <label className="block">
          <span className="text-gray-700">Total amount</span>
          <input className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2" defaultValue="$4,320.00" />
        </label>
        <label className="block">
          <span className="text-gray-700">Vendor name</span>
          <input className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2" defaultValue="Acme Corp" />
        </label>
        <label className="block">
          <span className="text-gray-700">Invoice date</span>
          <input className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2" defaultValue="2025-12-10" />
        </label>
      </div>
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
