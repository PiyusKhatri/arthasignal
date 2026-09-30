import type { LucideIcon } from "lucide-react";
import { Building2, LayoutDashboard, Star, TrendingUp, Wallet } from "lucide-react";

export type NavItem = {
  label: string;
  description: string;
  href: string;
  icon: LucideIcon;
};

export type NavSection = {
  label: string;
  items: NavItem[];
};

export const NAV_SECTIONS: NavSection[] = [
  {
    label: "Overview",
    items: [
      {
        label: "Dashboard",
        description: "Market command center",
        href: "/dashboard",
        icon: LayoutDashboard,
      },
    ],
  },
  {
    label: "Market",
    items: [
      {
        label: "Market Intelligence",
        description: "Artha rankings and market regime",
        href: "/market-pulse",
        icon: TrendingUp,
      },
      {
        label: "Sectors",
        description: "Sector breadth and leadership",
        href: "/sectors",
        icon: Building2,
      },
    ],
  },
  {
    label: "Your workspace",
    items: [
      {
        label: "Watchlist",
        description: "Stocks you are tracking",
        href: "/watchlist",
        icon: Star,
      },
      {
        label: "Portfolio",
        description: "Holdings and paper positions",
        href: "/portfolio",
        icon: Wallet,
      },
    ],
  },
];

export const NAV_ITEMS: NavItem[] = NAV_SECTIONS.flatMap((section) => section.items);
