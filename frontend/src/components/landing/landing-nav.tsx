import Link from "next/link";
import { ThemeToggle } from "@/components/app-shell/theme-toggle";

export function LandingNav() {
  return (
    <header className="sticky top-0 z-20 border-b border-border bg-background/95 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6">
        <Link href="/" className="text-lg font-semibold tracking-tight text-text-primary">
          Artha<span className="text-accent-text">Signal</span>
        </Link>

        <nav className="hidden items-center gap-8 text-sm text-text-secondary md:flex">
          <a href="#value" className="transition-colors hover:text-text-primary">
            Product
          </a>
          <a href="#pulse" className="transition-colors hover:text-text-primary">
            Market pulse
          </a>
          <a href="#pricing" className="transition-colors hover:text-text-primary">
            Pricing
          </a>
        </nav>

        <div className="flex items-center gap-2">
          <ThemeToggle />
          <Link
            href="/login"
            className="hidden rounded-md px-3 py-2 text-sm font-medium text-text-secondary transition-colors hover:text-text-primary sm:inline-block"
          >
            Log in
          </Link>
          <Link
            href="/signup"
            className="rounded-md bg-accent-primary px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-primary-light"
          >
            Get started
          </Link>
        </div>
      </div>
    </header>
  );
}
