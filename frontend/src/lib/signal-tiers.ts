export type SignalValidation = {
  status: string;
  graded_calls: number;
  required_calls: number | null;
  independent_entry_days: number;
  required_entry_days: number;
};

const TIER_LABELS: Record<string, string> = {
  under_validation: "Under validation",
  high_confidence: "Under validation",
  weak_or_no_edge: "Weak / no edge",
  unreliable_low_sample: "Unreliable — low sample",
  inconsistent_across_horizons: "Inconsistent across horizons",
  decayed_edge: "Decayed edge",
  liquidity_inverted: "Liquidity inverted",
  unstable_multi_dimensional: "Unstable (multi-dimensional)",
};

export const NEUTRAL_TIER_CLASS = "bg-border text-text-secondary";

export function tierLabel(tier: string | null): string {
  if (tier === null) {
    return "Unrated";
  }
  return TIER_LABELS[tier] ?? tier.replace(/_/g, " ");
}

export function validationEvidence(validation: SignalValidation | null | undefined): string | null {
  if (!validation) {
    return null;
  }
  const required = validation.required_calls ?? "?";
  return `${validation.graded_calls}/${required} graded calls`;
}

export function signalStatusLabel(tier: string | null, validation?: SignalValidation | null): string {
  const evidence = validationEvidence(validation);
  return evidence ? `${tierLabel(tier)} · ${evidence}` : tierLabel(tier);
}

export function signalStatusTitle(signalName: string, validation?: SignalValidation | null): string {
  if (!validation) {
    return signalName;
  }
  return `${signalName} — live paper-trade evidence: ${validation.graded_calls}/${validation.required_calls ?? "?"} graded calls, ${validation.independent_entry_days}/${validation.required_entry_days} entry days`;
}
