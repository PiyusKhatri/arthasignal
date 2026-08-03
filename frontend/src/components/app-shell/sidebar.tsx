import { NAV_ITEMS } from "@/lib/nav-items";
import { SidebarNavLink } from "@/components/app-shell/nav-link";

export function Sidebar() {
  return (
    <aside className="hidden w-16 shrink-0 flex-col border-r border-border bg-card px-2 py-4 md:flex lg:w-60 lg:px-3">
      <nav className="flex flex-col gap-1">
        {NAV_ITEMS.map((item) => (
          <SidebarNavLink key={item.href} href={item.href} />
        ))}
      </nav>
    </aside>
  );
}
