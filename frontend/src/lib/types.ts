export type Document = {
  id: number;
  status: string;
  doc_type: string;
  approved_at?: string | null;
  created_at?: string;
  ocr_text?: string;
  confidence?: number | string;
  extracted_data?: ExtractedData;
};

export type ReviewTask = {
  id: number;
  document: Document | number;
  assigned_to: number | null;
  status: string;
  created_at?: string;
  completed_at?: string | null;
  priority?: string;
};

export type ExtractedData = {
  id: number;
  raw_extraction?: any;
  invoice_number?: string;
  invoice_date?: string;
  due_date?: string;
  vendor_name?: string;
  total_amount?: string;
  currency?: string;
  line_items?: any[];
  overall_confidence?: number;
  field_confidences?: Record<string, number>;
};
