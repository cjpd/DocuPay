import type { NextConfig } from "next";

// Static export: the app is plain HTML/JS served from a CDN or S3 (no Node server to pay for).
// All data comes from the Django API at NEXT_PUBLIC_API_BASE.
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default nextConfig;
