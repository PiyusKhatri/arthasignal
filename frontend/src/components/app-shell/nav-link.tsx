"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAV_ITEMS } from "@/lib/nav-items";

function useNavItem(href: string) {
  const item = NAV_ITEMS.find((navItem) => navItem.href === href);
  if (!item) {
    throw new Error(`Unknown nav href: ${href}`);
  }
  return item;
}

function useIsActiveNavItem(href: string): boolean {
  const pathname = usePathname();
  if (href === "/dashboard") return pathname === href;
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function SidebarNavLink({ href, compact = false }: { href: string; compact?: boolean }) {
  const item = useNavItem(href);
  const isActive = useIsActiveNavItem(href);
  const Icon = item.icon;

  return (
    <Link
      href={item.href}
      aria-current={isActive ? "page" : undefined}
      title={compact ? `${item.label} — ${item.description}` : undefined}
      className={`group relative flex min-h-11 items-center rounded-xl text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-primary ${
        compact ? "justify-center px-2" : "gap-3 px-3"
      } ${
        isActive
          ? "bg-accent-text/10 text-accent-text"
          : "text-text-secondary hover:bg-background hover:text-text-primary"
      }`}
    >
      {isActive ? <span className="absolute left-0 h-5 w-0.5 rounded-r-full bg-accent-primary" aria-hidden="true" /> : null}
      <Icon className="size-[18px] shrink-0" strokeWidth={isActive ? 2.2 : 1.8} aria-hidden="true" />
      {!compact ? (
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium">{item.label}</span>
          <span className={`mt-0.5 block truncate text-[10px] ${isActive ? "text-accent-text/80" : "text-text-secondary"}`}>
            {item.description}
          </span>
        </span>
      ) : (
        <span className="pointer-events-none absolute left-full z-50 ml-3 hidden w-max max-w-56 rounded-lg border border-border bg-card px-3 py-2 text-left shadow-xl group-hover:block group-focus-visible:block">
          <span className="block text-xs font-semibold text-text-primary">{item.label}</span>
          <span className="mt-0.5 block text-[10px] font-normal text-text-secondary">{item.description}</span>
        </span>
      )}
    </Link>
  );
}

export function DrawerNavLink({ href, onNavigate }: { href: string; onNavigate: () => void }) {
  const item = useNavItem(href);
  const isActive = useIsActiveNavItem(href);
  const Icon = item.icon;

  return (
    <Link
      href={item.href}
      onClick={onNavigate}
      aria-current={isActive ? "page" : undefined}
      className={`relative flex min-h-12 items-center gap-3 rounded-xl px-3 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-primary ${
        isActive
          ? "bg-accent-text/10 text-accent-text"
          : "text-text-secondary hover:bg-background hover:text-text-primary"
      }`}
    >
      {isActive ? <span className="absolute left-0 h-6 w-0.5 rounded-r-full bg-accent-primary" aria-hidden="true" /> : null}
      <Icon className="size-5 shrink-0" strokeWidth={isActive ? 2.2 : 1.8} aria-hidden="true" />
      <span className="min-w-0">
        <span className="block truncate font-medium">{item.label}</span>
        <span className="mt-0.5 block truncate text-[10px] text-text-secondary">{item.description}</span>
      </span>
    </Link>
  );
}
