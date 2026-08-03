"use client";

import { createContext, use, useCallback, useState, type ReactNode } from "react";

type ShellContextValue = {
  isMobileNavOpen: boolean;
  openMobileNav: () => void;
  closeMobileNav: () => void;
};

const ShellContext = createContext<ShellContextValue | null>(null);

export function ShellProvider({ children }: { children: ReactNode }) {
  const [isMobileNavOpen, setIsMobileNavOpen] = useState(false);

  const openMobileNav = useCallback(() => setIsMobileNavOpen(true), []);
  const closeMobileNav = useCallback(() => setIsMobileNavOpen(false), []);

  return (
    <ShellContext.Provider value={{ isMobileNavOpen, openMobileNav, closeMobileNav }}>
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
