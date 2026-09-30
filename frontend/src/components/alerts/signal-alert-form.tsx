"use client";

import { useState, type FormEvent } from "react";
import { FormError } from "@/components/auth/form-error";
import { SubmitButton } from "@/components/auth/submit-button";
import { SymbolSearchInput } from "@/components/portfolio/symbol-search-input";
import { parseApiErrorMessage } from "@/lib/api-error";
import { TARGET_SIGNAL_NAMES } from "@/lib/alert-signals";

export function SignalAlertForm({ onSaved, onCancel }: { onSaved: () => void; onCancel: () => void }) {
  const [anySymbol, setAnySymbol] = useState(true);
  const [symbol, setSymbol] = useState("");
  const [signalName, setSignalName] = useState(TARGET_SIGNAL_NAMES[0]);
  const [symbolError, setSymbolError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);

    if (!anySymbol && symbol.trim().length === 0) {
      setSymbolError("Symbol is required");
      return;
    }
    setSymbolError(null);

    setPending(true);
    try {
      const response = await fetch("/api/alerts/signal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: anySymbol ? null : symbol.trim().toUpperCase(),
          signal_name: signalName,
        }),
      });
      const data = await response.json();
      if (!response.ok) {
        setFormError(parseApiErrorMessage(data, "Could not create signal alert. Please try again."));
        return;
      }
      onSaved();
    } catch {
      setFormError("Something went wrong. Please try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <form onSubmit={handleSubmit} noValidate>
        <FormError message={formError} />
        <div className="mb-4">
          <label htmlFor="signal-alert-name" className="mb-1 block text-sm text-text-secondary">
            Signal
          </label>
          <select
            id="signal-alert-name"
            value={signalName}
            onChange={(event) => setSignalName(event.target.value)}
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent-primary"
          >
            {TARGET_SIGNAL_NAMES.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </div>
        <label className="mb-2 flex items-center gap-2 text-sm text-text-secondary">
          <input
            type="checkbox"
            checked={anySymbol}
            onChange={(event) => setAnySymbol(event.target.checked)}
            className="size-4 rounded border-border"
          />
          Notify for any symbol
        </label>
        {!anySymbol ? <SymbolSearchInput value={symbol} onSelect={setSymbol} error={symbolError} /> : null}
        <div className="flex items-center gap-3">
          <div className="w-40">
            <SubmitButton label="Create alert" pending={pending} />
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary transition-colors hover:text-text-primary"
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
