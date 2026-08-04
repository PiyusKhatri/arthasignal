"use client";

import { useState, type FormEvent } from "react";
import { FormField } from "@/components/auth/form-field";
import { FormError } from "@/components/auth/form-error";
import { SubmitButton } from "@/components/auth/submit-button";
import { SymbolSearchInput } from "@/components/portfolio/symbol-search-input";
import { parseApiErrorMessage } from "@/lib/api-error";
import { validateDate, validatePositiveNumber, validateSymbol } from "@/lib/validation";

export function AddHoldingPanel({ onSaved, onCancel }: { onSaved: () => void; onCancel: () => void }) {
  const [symbol, setSymbol] = useState("");
  const [quantity, setQuantity] = useState("");
  const [purchasePrice, setPurchasePrice] = useState("");
  const [purchaseDate, setPurchaseDate] = useState("");
  const [symbolError, setSymbolError] = useState<string | null>(null);
  const [quantityError, setQuantityError] = useState<string | null>(null);
  const [priceError, setPriceError] = useState<string | null>(null);
  const [dateError, setDateError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);

    const nextSymbolError = validateSymbol(symbol);
    const nextQuantityError = validatePositiveNumber(quantity, "Quantity");
    const nextPriceError = validatePositiveNumber(purchasePrice, "Purchase price");
    const nextDateError = validateDate(purchaseDate);
    setSymbolError(nextSymbolError);
    setQuantityError(nextQuantityError);
    setPriceError(nextPriceError);
    setDateError(nextDateError);
    if (nextSymbolError || nextQuantityError || nextPriceError || nextDateError) {
      return;
    }

    setPending(true);
    try {
      const response = await fetch("/api/portfolio/holdings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: symbol.trim().toUpperCase(),
          quantity,
          purchase_price: purchasePrice,
          purchase_date: purchaseDate,
        }),
      });
      const data = await response.json();
      if (!response.ok) {
        setFormError(parseApiErrorMessage(data, "Could not add holding. Please try again."));
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
        <SymbolSearchInput value={symbol} onSelect={setSymbol} error={symbolError} />
        <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-3">
          <FormField
            id="quantity"
            label="Quantity"
            type="number"
            value={quantity}
            onChange={setQuantity}
            error={quantityError}
            min="0"
            step="any"
          />
          <FormField
            id="purchase-price"
            label="Purchase price (NPR)"
            type="number"
            value={purchasePrice}
            onChange={setPurchasePrice}
            error={priceError}
            min="0"
            step="any"
          />
          <FormField
            id="purchase-date"
            label="Purchase date"
            type="date"
            value={purchaseDate}
            onChange={setPurchaseDate}
            error={dateError}
            max={new Date().toISOString().slice(0, 10)}
          />
        </div>
        <div className="flex items-center gap-3">
          <div className="w-40">
            <SubmitButton label="Add holding" pending={pending} />
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
