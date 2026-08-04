const TIER_LABELS: Record<string, string> = {
  high_confidence: "High confidence",
  weak_or_no_edge: "Weak / no edge",
  unreliable_low_sample: "Unreliable — low sample",
  inconsistent_across_horizons: "Inconsistent across horizons",
  decayed_edge: "Decayed edge",
  liquidity_inverted: "Liquidity inverted",
  unstable_multi_dimensional: "Unstable (multi-dimensional)",
};

export function tierLabel(tier: string | null): string {
  if (tier === null) {
    return "Unrated";
  }
  return TIER_LABELS[tier] ?? tier.replace(/_/g, " ");
}

export function isHighConfidenceTier(tier: string | null): boolean {
  return tier === "high_confidence";
}
