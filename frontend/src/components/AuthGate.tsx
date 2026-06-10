"use client";

import { ReactNode, useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import { getAccessToken } from "@/lib/auth";

export default function AuthGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!getAccessToken()) {
      router.replace(`/login?next=${encodeURIComponent(pathname || "/")}`);
    }
  }, [pathname, router]);

  if (typeof window !== "undefined" && !getAccessToken()) return null;
  return <>{children}</>;
}
