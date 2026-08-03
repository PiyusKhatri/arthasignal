import { LineChart, ScanEye, ShieldCheck } from "lucide-react";

const VALUE_PROPS = [
  {
    icon: LineChart,
    title: "Backtested",
    body: "Every signal is tested against years of real NEPSE price history before it ships.",
  },
  {
    icon: ShieldCheck,
    title: "Statistically verified",
    body: "Win rates are measured against a baseline, not cherry-picked from a good month.",
  },
  {
    icon: ScanEye,
    title: "Transparent track record",
    body: "The win rate and holding period behind every signal are visible, always.",
  },
];

export function ValueProps() {
  return (
    <section id="value" className="mx-auto max-w-7xl px-4 py-20 sm:px-6">
      <div className="grid gap-10 sm:grid-cols-3 sm:gap-8">
        {VALUE_PROPS.map(({ icon: Icon, title, body }) => (
          <div key={title}>
            <Icon className="size-6 text-accent-primary" aria-hidden="true" strokeWidth={1.75} />
            <h3 className="mt-4 text-base font-semibold text-text-primary">{title}</h3>
            <p className="mt-2 text-sm leading-relaxed text-text-secondary">{body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
