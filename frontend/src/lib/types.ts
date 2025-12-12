export type Document = {
  id: number;
  status: string;
  doc_type: string;
  created_at?: string;
  ocr_text?: string;
  confidence?: string;
};

export type ReviewTask = {
  id: number;
  document: number;
  assigned_to: number | null;
  status: string;
  created_at?: string;
  completed_at?: string | null;
  priority?: string;
};
