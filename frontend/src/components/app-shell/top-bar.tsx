"use client";

import { Bell, Menu, Search, UserCircle } from "lucide-react";
import { ThemeToggle } from "@/components/app-shell/theme-toggle";
import { useShell } from "@/components/app-shell/shell-context";

export function TopBar() {
  const { openMobileNav } = useShell();

  return (
    <header className="flex h-14 items-center gap-3 border-b border-border bg-background px-3 sm:px-4">
      <button
        type="button"
        onClick={openMobileNav}
        aria-label="Open navigation"
        className="flex size-9 shrink-0 items-center justify-center rounded-md text-text-secondary hover:bg-card hover:text-text-primary md:hidden"
      >
        <Menu className="size-5" aria-hidden="true" />
      </button>

      <div className="relative flex-1 max-w-md">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-text-secondary" aria-hidden="true" />
        <input
          type="search"
          placeholder="Search stocks..."
          aria-label="Search stocks"
          className="w-full rounded-md border border-border bg-card py-2 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-secondary focus:outline-none focus:ring-2 focus:ring-accent-primary"
        />
      </div>

      <div className="ml-auto flex items-center gap-1 sm:gap-2">
        <ThemeToggle />

        <button
          type="button"
          aria-label="Notifications"
          className="flex size-9 items-center justify-center rounded-md text-text-secondary transition-colors hover:bg-card hover:text-text-primary"
        >
          <Bell className="size-5" aria-hidden="true" />
        </button>

        <button
          type="button"
          aria-label="Account"
          className="flex size-9 items-center justify-center rounded-full text-text-secondary transition-colors hover:bg-card hover:text-text-primary"
        >
          <UserCircle className="size-6" aria-hidden="true" />
        </button>
      </div>
    </header>
  );
}
