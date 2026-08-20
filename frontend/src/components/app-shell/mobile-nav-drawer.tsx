"use client";

import { useEffect } from "react";
import { X } from "lucide-react";
import { DrawerNavLink } from "@/components/app-shell/nav-link";
import { ShellBrand } from "@/components/app-shell/shell-brand";
import { useShell } from "@/components/app-shell/shell-context";
import { NAV_SECTIONS } from "@/lib/nav-items";

export function MobileNavDrawer() {
  const { isMobileNavOpen, closeMobileNav } = useShell();

  useEffect(() => {
    if (!isMobileNavOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeMobileNav();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [closeMobileNav, isMobileNavOpen]);

  if (!isMobileNavOpen) return null;

  return (
    <div className="fixed inset-0 z-50 md:hidden" role="dialog" aria-modal="true" aria-label="Navigation menu">
      <button
        type="button"
        aria-label="Close navigation"
        className="absolute inset-0 bg-black/55 backdrop-blur-[1px]"
        onClick={closeMobileNav}
      />

      <div className="absolute inset-y-0 left-0 flex w-[min(86vw,320px)] flex-col border-r border-border bg-card shadow-2xl">
        <div className="flex h-16 items-center justify-between border-b border-border px-4">
          <ShellBrand onNavigate={closeMobileNav} />
          <button
            type="button"
            aria-label="Close navigation"
            onClick={closeMobileNav}
            className="flex size-9 items-center justify-center rounded-lg text-text-secondary transition-colors hover:bg-background hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-primary"
          >
            <X className="size-5" aria-hidden="true" />
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto px-3 py-4" aria-label="Mobile navigation">
          <div className="space-y-5">
            {NAV_SECTIONS.map((section) => (
              <section key={section.label} aria-label={section.label}>
                <p className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-[0.14em] text-text-secondary">
                  {section.label}
                </p>
                <div className="space-y-1">
                  {section.items.map((item) => (
                    <DrawerNavLink key={item.href} href={item.href} onNavigate={closeMobileNav} />
                  ))}
                </div>
              </section>
            ))}
          </div>
        </nav>

        <div className="border-t border-border p-4">
          <div className="rounded-xl border border-border bg-background p-3">
            <p className="text-xs font-medium text-text-primary">ArthaSignal research workspace</p>
            <p className="mt-1 text-[10px] leading-relaxed text-text-secondary">
              Search stocks from the header, then use Market Intelligence to compare setup quality across NEPSE.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
