"use client";

import { Bell, Menu, UserCircle } from "lucide-react";
import { StockSearch } from "@/components/app-shell/stock-search";
import { ThemeToggle } from "@/components/app-shell/theme-toggle";
import { useShell } from "@/components/app-shell/shell-context";

export function TopBar() {
  const { openMobileNav } = useShell();

  return (
    <header className="sticky top-0 z-40 flex h-16 items-center gap-3 border-b border-border bg-background/95 px-3 backdrop-blur sm:px-4">
      <button
        type="button"
        onClick={openMobileNav}
        aria-label="Open navigation"
        className="flex size-9 shrink-0 items-center justify-center rounded-lg text-text-secondary transition-colors hover:bg-card hover:text-text-primary md:hidden"
      >
        <Menu className="size-5" aria-hidden="true" />
      </button>

      <div className="min-w-0 flex-1 sm:max-w-xl">
        <StockSearch />
      </div>

      <div className="ml-auto flex shrink-0 items-center gap-1 sm:gap-2">
        <ThemeToggle />

        <button
          type="button"
          aria-label="Notifications"
          className="flex size-9 items-center justify-center rounded-lg text-text-secondary transition-colors hover:bg-card hover:text-text-primary"
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
