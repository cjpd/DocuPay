"use client";

import { useRouter } from "next/navigation";
import { clearTokens } from "@/lib/auth";
import { useCurrentUser } from "@/lib/useCurrentUser";

const links = [
  { href: "/", label: "Dashboard" },
  { href: "/documents", label: "Documents" },
  { href: "/review", label: "Review" },
  { href: "/settings/webhooks", label: "Webhooks" },
];

export default function Navbar() {
  const router = useRouter();
  const { data: user } = useCurrentUser();

  const handleLogout = () => {
    clearTokens();
    router.push("/login");
  };

  return (
    <nav className="w-full bg-white border-b px-6 py-3 flex items-center justify-between shadow-sm">
      <div className="flex items-center gap-8">
        <span className="font-semibold text-gray-900">IDP Platform</span>
        <div className="hidden md:flex items-center gap-4 text-sm text-gray-700">
          {links.map((link) => (
            <a key={link.href} href={link.href} className="hover:text-gray-900">
              {link.label}
            </a>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-3 text-sm">
        {user ? (
          <>
            <span className="text-gray-700">Hi, {user.username}</span>
            <button
              onClick={handleLogout}
              className="rounded-md border border-gray-200 px-3 py-1.5 hover:bg-gray-50"
            >
              Log out
            </button>
          </>
        ) : (
          <button
            onClick={() => router.push("/login")}
            className="rounded-md border border-gray-200 px-3 py-1.5 hover:bg-gray-50"
          >
            Log in
          </button>
        )}
      </div>
    </nav>
  );
}
