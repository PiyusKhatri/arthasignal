"use client";

import { useState } from "react";
import { AlertsEmptyState } from "@/components/alerts/alerts-empty-state";
import { PriceAlertForm } from "@/components/alerts/price-alert-form";
import { PriceAlertsList } from "@/components/alerts/price-alerts-list";
import { SignalAlertForm } from "@/components/alerts/signal-alert-form";
import { SignalAlertsList } from "@/components/alerts/signal-alerts-list";
import type { PriceAlert, SignalAlert } from "@/lib/alerts-data";

export function AlertsClient({
  initialPriceAlerts,
  initialSignalAlerts,
}: {
  initialPriceAlerts: PriceAlert[];
  initialSignalAlerts: SignalAlert[];
}) {
  const [priceAlerts, setPriceAlerts] = useState(initialPriceAlerts);
  const [signalAlerts, setSignalAlerts] = useState(initialSignalAlerts);
  const [showPriceForm, setShowPriceForm] = useState(false);
  const [showSignalForm, setShowSignalForm] = useState(false);

  async function refreshPriceAlerts() {
    const response = await fetch("/api/alerts/price", { cache: "no-store" });
    if (response.ok) {
      setPriceAlerts((await response.json()) as PriceAlert[]);
    }
  }

  async function refreshSignalAlerts() {
    const response = await fetch("/api/alerts/signal", { cache: "no-store" });
    if (response.ok) {
      setSignalAlerts((await response.json()) as SignalAlert[]);
    }
  }

  return (
    <div className="flex flex-col gap-8">
      <section>
        <h1 className="text-lg font-semibold text-text-primary">Alerts</h1>
      </section>

      <section>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-text-primary">Price Alerts</h2>
          <button
            type="button"
            onClick={() => setShowPriceForm((value) => !value)}
            className="rounded-md bg-accent-primary px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-primary-light"
          >
            {showPriceForm ? "Cancel" : "Create alert"}
          </button>
        </div>

        {showPriceForm ? (
          <div className="mt-4">
            <PriceAlertForm
              onSaved={() => {
                setShowPriceForm(false);
                refreshPriceAlerts();
              }}
              onCancel={() => setShowPriceForm(false)}
            />
          </div>
        ) : null}

        <div className="mt-4">
          {priceAlerts.length > 0 ? (
            <PriceAlertsList alerts={priceAlerts} onChanged={refreshPriceAlerts} />
          ) : !showPriceForm ? (
            <AlertsEmptyState label="No price alerts yet. Create one to get notified when a stock hits your target." />
          ) : null}
        </div>
      </section>

      <section>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-text-primary">Signal Alerts</h2>
          <button
            type="button"
            onClick={() => setShowSignalForm((value) => !value)}
            className="rounded-md bg-accent-primary px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-primary-light"
          >
            {showSignalForm ? "Cancel" : "Create alert"}
          </button>
        </div>

        {showSignalForm ? (
          <div className="mt-4">
            <SignalAlertForm
              onSaved={() => {
                setShowSignalForm(false);
                refreshSignalAlerts();
              }}
              onCancel={() => setShowSignalForm(false)}
            />
          </div>
        ) : null}

        <div className="mt-4">
          {signalAlerts.length > 0 ? (
            <SignalAlertsList alerts={signalAlerts} onChanged={refreshSignalAlerts} />
          ) : !showSignalForm ? (
            <AlertsEmptyState label="No signal alerts yet. Create one to get notified when a technical signal fires." />
          ) : null}
        </div>
      </section>
    </div>
  );
}
