"use client";

import { createContext, use, useCallback, useEffect, useState, type ReactNode } from "react";

type ShellContextValue = {
  isMobileNavOpen: boolean;
  openMobileNav: () => void;
  closeMobileNav: () => void;
  isSidebarCollapsed: boolean;
  toggleSidebar: () => void;
};

const SIDEBAR_KEY = "artha-sidebar-collapsed:v1";
const ShellContext = createContext<ShellContextValue | null>(null);

export function ShellProvider({ children }: { children: ReactNode }) {
  const [isMobileNavOpen, setIsMobileNavOpen] = useState(false);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);

  useEffect(() => {
    try {
      setIsSidebarCollapsed(window.localStorage.getItem(SIDEBAR_KEY) === "true");
    } catch {
      // Local storage can be unavailable in privacy-restricted environments.
    }
  }, []);

  const openMobileNav = useCallback(() => setIsMobileNavOpen(true), []);
  const closeMobileNav = useCallback(() => setIsMobileNavOpen(false), []);
  const toggleSidebar = useCallback(() => {
    setIsSidebarCollapsed((current) => {
      const next = !current;
      try {
        window.localStorage.setItem(SIDEBAR_KEY, String(next));
      } catch {
        // Keep the UI usable even when persistence is blocked.
      }
      return next;
    });
  }, []);

  return (
    <ShellContext.Provider
      value={{ isMobileNavOpen, openMobileNav, closeMobileNav, isSidebarCollapsed, toggleSidebar }}
    >
      {children}
    </ShellContext.Provider>
  );
}

export function useShell(): ShellContextValue {
  const context = use(ShellContext);
  if (context === null) {
    throw new Error("useShell must be used within a ShellProvider");
  }
  return context;
}
