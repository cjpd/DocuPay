"use client";

type Props = {
  src: string;
  fileName?: string;
};

function isPdf(src: string) {
  return src.split("?")[0].toLowerCase().endsWith(".pdf");
}

export default function PdfPreview({ src, fileName }: Props) {
  const pdf = isPdf(src);

  return (
    <div className="rounded-lg border border-gray-200 bg-white shadow-sm flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
        <h3 className="text-sm font-semibold text-gray-900 truncate">
          {fileName || "Document preview"}
        </h3>
        <a
          href={src}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-gray-500 hover:text-gray-800 shrink-0 ml-3"
        >
          Open full size ↗
        </a>
      </div>

      <div className="flex-1 min-h-0">
        {pdf ? (
          <iframe
            src={src}
            title={fileName || "Document"}
            className="w-full h-full min-h-[600px]"
            style={{ border: "none" }}
          />
        ) : (
          <div className="p-4">
            <img
              src={src}
              alt={fileName || "Document"}
              className="w-full h-auto max-h-[600px] object-contain rounded"
            />
          </div>
        )}
      </div>
    </div>
  );
}
