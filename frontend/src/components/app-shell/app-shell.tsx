import type { ReactNode } from "react";
import { ShellProvider } from "@/components/app-shell/shell-context";
import { Sidebar } from "@/components/app-shell/sidebar";
import { MobileNavDrawer } from "@/components/app-shell/mobile-nav-drawer";
import { TopBar } from "@/components/app-shell/top-bar";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <ShellProvider>
      <div className="flex min-h-screen bg-background">
        <Sidebar />
        <MobileNavDrawer />
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar />
          <main className="flex-1 p-4 sm:p-6">{children}</main>
        </div>
      </div>
    </ShellProvider>
  );
}
