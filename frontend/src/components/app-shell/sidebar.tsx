"use client";

import { ChevronLeft, ChevronRight, Search } from "lucide-react";
import { SidebarNavLink } from "@/components/app-shell/nav-link";
import { ShellBrand } from "@/components/app-shell/shell-brand";
import { useShell } from "@/components/app-shell/shell-context";
import { NAV_SECTIONS } from "@/lib/nav-items";

export function Sidebar() {
  const { isSidebarCollapsed, toggleSidebar } = useShell();

  return (
    <aside
      data-testid="desktop-sidebar"
      className={`sticky top-0 hidden h-screen shrink-0 flex-col border-r border-border bg-card transition-[width] duration-200 md:flex ${
        isSidebarCollapsed ? "w-[72px]" : "w-64"
      }`}
    >
      <div className={`flex h-16 items-center border-b border-border ${isSidebarCollapsed ? "justify-center px-2" : "px-4"}`}>
        <ShellBrand compact={isSidebarCollapsed} />
      </div>

      <nav className={`flex-1 overflow-y-auto py-4 ${isSidebarCollapsed ? "px-2" : "px-3"}`} aria-label="Primary navigation">
        <div className="space-y-5">
          {NAV_SECTIONS.map((section) => (
            <section key={section.label} aria-label={section.label}>
              {!isSidebarCollapsed ? (
                <p className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-[0.14em] text-text-secondary">
                  {section.label}
                </p>
              ) : null}
              <div className="space-y-1">
                {section.items.map((item) => (
                  <SidebarNavLink key={item.href} href={item.href} compact={isSidebarCollapsed} />
                ))}
              </div>
            </section>
          ))}
        </div>
      </nav>

      <div className={`border-t border-border p-3 ${isSidebarCollapsed ? "space-y-2" : "space-y-3"}`}>
        {!isSidebarCollapsed ? (
          <div className="rounded-xl border border-border bg-background p-3">
            <div className="flex items-center gap-2 text-xs font-medium text-text-primary">
              <Search className="size-3.5 text-accent-text" aria-hidden="true" />
              Find any NEPSE stock
            </div>
            <p className="mt-1.5 text-[10px] leading-relaxed text-text-secondary">
              Use the search bar above or press ⌘K / Ctrl+K from anywhere.
            </p>
          </div>
        ) : null}

        <button
          type="button"
          onClick={toggleSidebar}
          aria-label={isSidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          data-testid="sidebar-collapse"
          className={`flex min-h-9 w-full items-center rounded-lg text-xs font-medium text-text-secondary transition-colors hover:bg-background hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-primary ${
            isSidebarCollapsed ? "justify-center" : "justify-between px-2.5"
          }`}
        >
          {!isSidebarCollapsed ? <span>Collapse navigation</span> : null}
          {isSidebarCollapsed ? <ChevronRight className="size-4" aria-hidden="true" /> : <ChevronLeft className="size-4" aria-hidden="true" />}
        </button>
      </div>
    </aside>
  );
}
