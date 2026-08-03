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
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function SidebarNavLink({ href }: { href: string }) {
  const item = useNavItem(href);
  const isActive = useIsActiveNavItem(href);
  const Icon = item.icon;

  return (
    <Link
      href={item.href}
      aria-current={isActive ? "page" : undefined}
      title={item.label}
      className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${
        isActive
          ? "bg-accent-primary/10 text-accent-primary"
          : "text-text-secondary hover:bg-card hover:text-text-primary"
      }`}
    >
      <Icon className="size-5 shrink-0" aria-hidden="true" />
      <span className="hidden truncate lg:inline">{item.label}</span>
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
      className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${
        isActive
          ? "bg-accent-primary/10 text-accent-primary"
          : "text-text-secondary hover:bg-card hover:text-text-primary"
      }`}
    >
      <Icon className="size-5 shrink-0" aria-hidden="true" />
      <span className="truncate">{item.label}</span>
    </Link>
  );
}
