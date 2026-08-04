const SIGNAL_PLAIN_LANGUAGE: Record<string, string> = {
  "rsi_14 < 30 (oversold)": "Price has dropped quickly and may be due for a bounce.",
  "rsi_14 > 70 (overbought)": "Price has risen quickly and may be due for a pause.",
  "close < bollinger_lower": "Price closed lower than its normal recent range — an unusually sharp move down.",
  doji: "Buyers and sellers fought to a draw today — price opened and closed at nearly the same level.",
};

export function plainLanguageSignalLabel(signalName: string): string {
  return SIGNAL_PLAIN_LANGUAGE[signalName] ?? signalName;
}

export function hasPlainLanguageLabel(signalName: string): boolean {
  return signalName in SIGNAL_PLAIN_LANGUAGE;
}
