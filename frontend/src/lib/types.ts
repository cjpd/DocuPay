export type Status = "pending" | "processing" | "requires_review" | "approved" | "processed" | "failed";

export type Check = {
  name: string;
  status: "pass" | "fail" | "skip";
  severity: "critical" | "major" | "minor";
  message: string;
  fields: string[];
};

export type LineItem = {
  description: string | null;
  quantity: string | null;
  unit_price: string | null;
  amount: string | null;
};

export type VendorSummary = {
  id: number; name: string; tax_id: string; default_currency: string; bank_account: string; is_blocked: boolean;
};

export type Vendor = VendorSummary & {
  bank_code: string;
  aliases: string[];
  notes: string;
  invoice_count: number;
  last_invoice_at: string | null;
  created_at: string;
};

export type ExtractedData = {
  id: number;
  invoice_number: string;
  vendor_tax_id: string;
  vendor: number | null;
  vendor_detail: VendorSummary | null;
  invoice_date: string | null;
  due_date: string | null;
  vendor_name: string;
  customer_name: string;
  purchase_order: string;
  subtotal: string | null;
  tax_amount: string | null;
  total_amount: string | null;
  amount_due: string | null;
  bank_account: string;
  bank_code: string;
  currency: string;
  line_items: LineItem[];
  overall_confidence: number;
  field_confidences: Record<string, number>;
  validation: Check[];
};

export type ProcessingMeta = {
  source?: string;
  decision?: "auto_approve" | "review";
  escalated?: boolean;
  total_cost_usd?: string | null;
  attempts?: { tier: string; model: string; cost_usd: string | null; failed_checks: string[] }[];
};

export type Doc = {
  id: number;
  organization: number;
  file_name: string;
  review_task_id: number | null;
  status: Status;
  doc_type: string;
  confidence: number | null;
  extracted_data: ExtractedData | null;
  approved_at: string | null;
  page_count: number | null;
  error_message: string;
  processing_meta: ProcessingMeta;
  created_at: string;
  updated_at: string;
};

export type ReviewTask = {
  id: number;
  document: number;
  document_detail: Doc;
  status: "pending" | "approved" | "rejected";
  reviewed_at: string | null;
  created_at: string;
};

export type Page<T> = { count: number; next: string | null; previous: string | null; results: T[] };

export type Stats = {
  days: number;
  received: number;
  auto_approved: number;
  approved_by_person: number;
  needs_review: number;
  failed: number;
  in_progress: number;
  straight_through_rate: number | null;
  approved_value: Record<string, string>;
  processing_cost_usd: string;
  series: { date: string; received: number; auto_approved: number }[];
};

export type Organization = {
  id: number;
  name: string;
  slug: string;
  auto_approve_threshold: number;
  auto_approve_max_amount: string | null;
  review_new_vendors: boolean;
  my_role: string | null;
};

export type User = { id: number; username: string; email: string; first_name: string; last_name: string };

export type WebhookEvent = "invoice.approved" | "invoice.needs_review" | "invoice.rejected" | "invoice.failed";

export type Webhook = {
  id: number;
  target_url: string;
  description: string;
  events: WebhookEvent[];
  is_active: boolean;
  signing_secret: string | null;
  last_delivery: { status: string; status_code: number | null; at: string; event_type: string } | null;
  created_at: string;
};

export type WebhookDelivery = {
  id: number;
  webhook_config: number;
  document: number | null;
  event_id: string;
  event_type: string;
  status: "pending" | "delivered" | "failed";
  status_code: number | null;
  attempts: number;
  last_error: string;
  response_ms: number | null;
  next_attempt_at: string | null;
  created_at: string;
  updated_at: string;
};
