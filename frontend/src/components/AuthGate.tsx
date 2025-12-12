"use client";

import { ReactNode, useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import { getAccessToken } from "@/lib/auth";

export default function AuthGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const hasToken = typeof window !== "undefined" ? !!getAccessToken() : true; // assume true during SSR to avoid hydration mismatch

  useEffect(() => {
    if (typeof window === "undefined") return;
    const token = getAccessToken();
    if (!token) {
      const next = encodeURIComponent(pathname || "/");
      router.replace(`/login?next=${next}`);
    }
  }, [pathname, router]);

  return <>{children}</>;
}
