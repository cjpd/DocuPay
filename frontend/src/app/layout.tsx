import "./globals.css";
import type { ReactNode } from "react";
import Navbar from "@/components/Navbar";
import Providers from "./providers";

export const metadata = {
  title: "IDP Platform",
  description: "AI-powered Intelligent Document Processing",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-gray-50 text-gray-900 min-h-screen">
        <Providers>
          <div className="flex flex-col min-h-screen">
            <Navbar />
            <div className="flex-1">{children}</div>
          </div>
        </Providers>
      </body>
    </html>
  );
}
