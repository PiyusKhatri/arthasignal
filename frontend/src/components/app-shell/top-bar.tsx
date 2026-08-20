"use client";

import { Menu } from "lucide-react";
import { usePathname } from "next/navigation";
import { ShellBrand } from "@/components/app-shell/shell-brand";
import { StockSearch } from "@/components/app-shell/stock-search";
import { ThemeToggle } from "@/components/app-shell/theme-toggle";
import { useShell } from "@/components/app-shell/shell-context";

type PageContext = { eyebrow: string; title: string };

function getPageContext(pathname: string): PageContext {
  if (pathname === "/dashboard") return { eyebrow: "Overview", title: "Dashboard" };
  if (pathname.startsWith("/market-pulse")) return { eyebrow: "Market", title: "Market Intelligence" };
  if (pathname.startsWith("/sectors")) return { eyebrow: "Market", title: "Sectors" };
  if (pathname.startsWith("/watchlist")) return { eyebrow: "Workspace", title: "Watchlist" };
  if (pathname.startsWith("/portfolio")) return { eyebrow: "Workspace", title: "Portfolio" };
  if (pathname.startsWith("/stock/")) {
    const encoded = pathname.split("/")[2] ?? "Stock";
    let symbol = encoded;
    try {
      symbol = decodeURIComponent(encoded).toUpperCase();
    } catch {
      symbol = encoded.toUpperCase();
    }
    return { eyebrow: "Stock analysis", title: symbol };
  }
  return { eyebrow: "ArthaSignal", title: "Research workspace" };
}

export function TopBar() {
  const { openMobileNav } = useShell();
  const pathname = usePathname();
  const context = getPageContext(pathname);

  return (
    <header data-testid="app-topbar" className="sticky top-0 z-40 border-b border-border bg-background/95 backdrop-blur">
      <div className="mx-auto w-full max-w-[1760px] px-3 sm:px-5 lg:px-7">
        <div className="flex h-16 items-center gap-3 md:gap-5">
          <button
            type="button"
            onClick={openMobileNav}
            aria-label="Open navigation"
            className="flex size-9 shrink-0 items-center justify-center rounded-lg text-text-secondary transition-colors hover:bg-card hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-primary md:hidden"
          >
            <Menu className="size-5" aria-hidden="true" />
          </button>

          <div className="min-w-0 flex-1 md:hidden">
            <ShellBrand />
          </div>

          <div className="hidden min-w-[190px] md:block">
            <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-text-secondary">{context.eyebrow}</p>
            <p className="mt-0.5 truncate text-sm font-semibold text-text-primary">{context.title}</p>
          </div>

          <div className="hidden min-w-0 flex-1 justify-center md:flex">
            <div className="w-full max-w-xl">
              <StockSearch />
            </div>
          </div>

          <div className="ml-auto shrink-0">
            <ThemeToggle />
          </div>
        </div>

        <div className="pb-3 md:hidden">
          <StockSearch shortcutEnabled={false} />
        </div>
      </div>
    </header>
  );
}
