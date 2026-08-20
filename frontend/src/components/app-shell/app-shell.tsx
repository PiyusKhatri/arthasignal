import type { ReactNode } from "react";
import { MobileNavDrawer } from "@/components/app-shell/mobile-nav-drawer";
import { ShellProvider } from "@/components/app-shell/shell-context";
import { Sidebar } from "@/components/app-shell/sidebar";
import { TopBar } from "@/components/app-shell/top-bar";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <ShellProvider>
      <a
        href="#app-content"
        className="sr-only z-[100] rounded-lg bg-accent-primary px-3 py-2 text-sm font-medium text-white focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
      >
        Skip to content
      </a>

      <div className="flex min-h-screen bg-background">
        <Sidebar />
        <MobileNavDrawer />

        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar />
          <main id="app-content" tabIndex={-1} className="min-w-0 flex-1 focus:outline-none">
            <div className="mx-auto w-full max-w-[1760px] px-4 py-5 sm:px-5 sm:py-6 lg:px-7 lg:py-7 xl:px-8">
              {children}
            </div>
          </main>
        </div>
      </div>
    </ShellProvider>
  );
}
