import type { ReactNode } from "react";

export function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm rounded-lg border border-border bg-card p-6">
        <h1 className="mb-6 text-xl font-semibold text-text-primary">{title}</h1>
        {children}
      </div>
    </main>
  );
}
