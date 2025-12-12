"use client";

import { useState } from "react";
import { useUploadDocument } from "@/lib/useDocuments";

export default function DocumentUpload() {
  const [fileName, setFileName] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const upload = useUploadDocument();

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    setFile(selected || null);
    setFileName(selected ? selected.name : null);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (file) {
      upload.mutate(file);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <label className="block">
        <span className="text-sm font-medium text-gray-700">Upload PDF or image</span>
        <input
          type="file"
          accept=".pdf,.png,.jpg,.jpeg"
          className="mt-2 block w-full text-sm text-gray-700"
          onChange={handleFile}
        />
      </label>
      {fileName && <p className="text-sm text-gray-600">Selected: {fileName}</p>}
      <button
        type="submit"
        className="rounded-md bg-gray-900 text-white px-4 py-2 text-sm font-medium hover:bg-gray-800 disabled:opacity-60"
        disabled={!file || upload.isLoading}
      >
        {upload.isLoading ? "Uploading..." : "Upload"}
      </button>
      {upload.isSuccess && <p className="text-sm text-emerald-600">Uploaded and queued for processing.</p>}
      {upload.isError && <p className="text-sm text-red-600">Upload failed.</p>}
    </form>
  );
}
