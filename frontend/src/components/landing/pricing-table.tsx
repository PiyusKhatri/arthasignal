const TIERS = [
  { name: "Free", blurb: "Public charts, market pulse, and stock pages." },
  { name: "Trader", blurb: "Signal alerts, watchlists, and portfolio tracking." },
  { name: "Pro", blurb: "Everything in Trader, plus the AI chatbot and bot linking." },
];

export function PricingTable() {
  return (
    <section id="pricing" className="mx-auto max-w-7xl px-4 py-20 sm:px-6">
      <h2 className="text-2xl font-semibold tracking-tight text-text-primary sm:text-3xl">Pricing</h2>
      <p className="mt-2 max-w-md text-sm text-text-secondary">
        Plans are still being finalized. Sign up to be notified when they go live.
      </p>

      <div className="mt-10 grid grid-cols-1 gap-6 sm:grid-cols-3">
        {TIERS.map((tier) => (
          <div key={tier.name} className="rounded-lg border border-border bg-card p-6">
            <p className="text-sm font-medium text-text-primary">{tier.name}</p>
            <p className="mt-3 text-2xl font-semibold text-text-secondary">Coming soon</p>
            <p className="mt-3 text-sm leading-relaxed text-text-secondary">{tier.blurb}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
