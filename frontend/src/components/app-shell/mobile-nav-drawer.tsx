"use client";

import { X } from "lucide-react";
import { NAV_ITEMS } from "@/lib/nav-items";
import { DrawerNavLink } from "@/components/app-shell/nav-link";
import { useShell } from "@/components/app-shell/shell-context";

export function MobileNavDrawer() {
  const { isMobileNavOpen, closeMobileNav } = useShell();

  if (!isMobileNavOpen) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-50 md:hidden">
      <button
        type="button"
        aria-label="Close navigation"
        className="absolute inset-0 bg-black/50"
        onClick={closeMobileNav}
      />
      <nav className="absolute inset-y-0 left-0 flex w-64 flex-col gap-1 border-r border-border bg-card px-3 py-4">
        <div className="mb-2 flex items-center justify-between px-1">
          <span className="text-sm font-semibold text-text-primary">ArthaSignal</span>
          <button
            type="button"
            aria-label="Close navigation"
            onClick={closeMobileNav}
            className="rounded-md p-1 text-text-secondary hover:bg-background hover:text-text-primary"
          >
            <X className="size-5" aria-hidden="true" />
          </button>
        </div>
        {NAV_ITEMS.map((item) => (
          <DrawerNavLink key={item.href} href={item.href} onNavigate={closeMobileNav} />
        ))}
      </nav>
    </div>
  );
}
