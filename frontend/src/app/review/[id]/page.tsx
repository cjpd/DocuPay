import AuthGate from "@/components/AuthGate";
import ReviewForm from "@/components/ReviewForm";
import PdfPreview from "@/components/PdfPreview";
import { useParams } from "next/navigation";
import { useReviewActions, useReviewTask } from "@/lib/useReviewTask";

export default function ReviewDetailPage() {
  const params = useParams();
  const id = params?.id as string;
  const { data: task, isLoading } = useReviewTask(id);
  const { approve, reject } = useReviewActions(id);

  return (
    <AuthGate>
      <main className="p-8 space-y-4">
        <header className "flex items-center justify-between">
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
          <div className="rounded-lg border border-gray-200 bg-white shadow-sm p-4 text-gray-600">Loading review task...</div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            <PdfPreview />
            <ReviewForm
              onApprove={() => approve.mutate()}
              onReject={() => reject.mutate()}
              isApproving={approve.isLoading}
              isRejecting={reject.isLoading}
            />
          </div>
        )}
      </main>
    </AuthGate>
  );
}
