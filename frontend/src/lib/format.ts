import type { Check, Status } from "./types";

export function money(value: string | number | null | undefined, currency?: string) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  try {
    return new Intl.NumberFormat(undefined, {
      style: currency ? "currency" : "decimal",
      currency: currency || undefined,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(n);
  } catch {
    return `${n.toFixed(2)} ${currency || ""}`.trim();
  }
}

export function date(value: string | null | undefined) {
  if (!value) return "—";
  const d = new Date(value.length === 10 ? `${value}T00:00:00` : value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function ago(value: string) {
  const s = Math.round((Date.now() - new Date(value).getTime()) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return date(value);
}

export function percent(rate: number | null | undefined) {
  return rate === null || rate === undefined ? "—" : `${Math.round(rate * 100)}%`;
}

/** What each check proves, in the words of the person reviewing. */
export const CHECK_LABELS: Record<string, string> = {
  is_invoice: "It is an invoice",
  required_fields: "Vendor, number, date and total are present",
  positive_total: "The total is a positive amount",
  totals_math: "Subtotal + tax = total",
  line_items_sum: "Line items add up",
  line_item_math: "Quantity × price = line amount",
  date_order: "Dates make sense",
  currency: "Currency is known",
  model_uncertain: "Every field was read clearly",
  amount_limit: "Amount is within your limit",
  duplicate: "Not a duplicate",
  new_vendor: "Vendor has been approved before",
  vendor_history: "Matches this vendor's usual invoices",
  vendor_not_self: "Vendor is not your own company",
  amounts_verified: "The total can be verified",
  vendor_master: "Matches your vendor list",
};

/** The same checks, worded for when they fail. */
export const FAIL_LABELS: Record<string, string> = {
  is_invoice: "This may not be an invoice",
  required_fields: "Something essential is missing",
  positive_total: "Credit note or zero total",
  totals_math: "Subtotal + tax does not equal the total",
  line_items_sum: "Line items do not add up",
  line_item_math: "A line's quantity × price is off",
  date_order: "A date looks wrong",
  currency: "Currency is missing or unknown",
  model_uncertain: "Part of the document was hard to read",
  amount_limit: "Above your auto-approval limit",
  duplicate: "Possible duplicate",
  new_vendor: "First invoice from this vendor",
  vendor_history: "Unusual for this vendor",
  vendor_not_self: "The vendor looks like your own company",
  amounts_verified: "The total could not be verified",
  vendor_master: "Does not match your vendor list",
};

export const checkLabel = (c: Check) =>
  (c.status === "fail" ? FAIL_LABELS[c.name] : CHECK_LABELS[c.name]) || c.name.replaceAll("_", " ");

export const FIELD_LABELS: Record<string, string> = {
  vendor_name: "Vendor",
  vendor_tax_id: "Vendor tax ID",
  invoice_number: "Invoice number",
  invoice_date: "Invoice date",
  due_date: "Due date",
  customer_name: "Billed to",
  purchase_order: "PO number",
  currency: "Currency",
  subtotal: "Subtotal",
  tax_amount: "Tax",
  total_amount: "Total",
};

export const STATUS: Record<Status, { label: string; tone: "ok" | "warn" | "bad" | "info" }> = {
  pending: { label: "Queued", tone: "info" },
  processing: { label: "Reading", tone: "info" },
  requires_review: { label: "Needs you", tone: "warn" },
  approved: { label: "Approved", tone: "ok" },
  processed: { label: "Processed", tone: "ok" },
  failed: { label: "Failed", tone: "bad" },
};
