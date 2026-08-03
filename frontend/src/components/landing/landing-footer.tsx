import Link from "next/link";

export function LandingFooter() {
  const year = new Date().getFullYear();

  return (
    <footer className="border-t border-border">
      <div className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-10 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <Link href="/" className="text-sm font-semibold tracking-tight text-text-primary">
          Artha<span className="text-accent-primary">Signal</span>
        </Link>

        <nav className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-text-secondary">
          <a href="#value" className="transition-colors hover:text-text-primary">
            Product
          </a>
          <a href="#pulse" className="transition-colors hover:text-text-primary">
            Market pulse
          </a>
          <a href="#pricing" className="transition-colors hover:text-text-primary">
            Pricing
          </a>
          <Link href="/privacy" className="transition-colors hover:text-text-primary">
            Privacy
          </Link>
          <Link href="/terms" className="transition-colors hover:text-text-primary">
            Terms
          </Link>
        </nav>

        <p className="text-sm text-text-secondary">© {year} ArthaSignal.</p>
      </div>
    </footer>
  );
}
