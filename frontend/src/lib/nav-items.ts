import type { LucideIcon } from "lucide-react";
import { Bell, LayoutDashboard, Search, Settings, TrendingUp, Wallet, Star } from "lucide-react";

export type NavItem = {
  label: string;
  href: string;
  icon: LucideIcon;
};

export const NAV_ITEMS: NavItem[] = [
  { label: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
  { label: "Stock Search", href: "/search", icon: Search },
  { label: "Watchlist", href: "/watchlist", icon: Star },
  { label: "Portfolio", href: "/portfolio", icon: Wallet },
  { label: "Market Pulse", href: "/market-pulse", icon: TrendingUp },
  { label: "Alerts", href: "/alerts", icon: Bell },
  { label: "Settings", href: "/settings", icon: Settings },
];
