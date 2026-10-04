import { AlertTriangle, CheckCircle2, Loader2, XCircle, Clock } from "lucide-react";
import { STATUS } from "@/lib/format";
import type { Doc } from "@/lib/types";
import { Badge } from "./ui";

const ICONS = {
  pending: Clock,
  processing: Loader2,
  requires_review: AlertTriangle,
  approved: CheckCircle2,
  processed: CheckCircle2,
  failed: XCircle,
};

export function StatusBadge({ doc }: { doc: Pick<Doc, "status" | "processing_meta"> }) {
  const s = STATUS[doc.status];
  const Icon = ICONS[doc.status];
  const auto = doc.status === "approved" && doc.processing_meta?.decision === "auto_approve";
  return (
    <Badge tone={s.tone}>
      <Icon aria-hidden className={doc.status === "processing" ? "size-3.5 animate-spin" : "size-3.5"} />
      {auto ? "Auto-approved" : s.label}
    </Badge>
  );
}
