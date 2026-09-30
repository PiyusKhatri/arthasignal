import { redirect } from "next/navigation";
import { AlertsClient } from "@/components/alerts/alerts-client";
import { getPriceAlerts, getSignalAlerts } from "@/lib/alerts-data";

export default async function AlertsPage() {
  const [{ alerts: priceAlerts, unauthorized: priceUnauthorized }, { alerts: signalAlerts, unauthorized: signalUnauthorized }] =
    await Promise.all([getPriceAlerts(), getSignalAlerts()]);

  if (priceUnauthorized || signalUnauthorized || !priceAlerts || !signalAlerts) {
    redirect("/login?next=/alerts");
  }

  return <AlertsClient initialPriceAlerts={priceAlerts} initialSignalAlerts={signalAlerts} />;
}
