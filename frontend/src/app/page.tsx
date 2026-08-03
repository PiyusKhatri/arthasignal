import { Hero } from "@/components/landing/hero";
import { LandingFooter } from "@/components/landing/landing-footer";
import { LandingNav } from "@/components/landing/landing-nav";
import { MarketPulseWidget } from "@/components/landing/market-pulse-widget";
import { PricingTable } from "@/components/landing/pricing-table";
import { ValueProps } from "@/components/landing/value-props";
import { getHeroStockData, getMarketPulseData } from "@/lib/landing-data";

export default async function Home() {
  const [stock, pulse] = await Promise.all([getHeroStockData(), getMarketPulseData()]);

  return (
    <div className="min-h-[100dvh] bg-background">
      <LandingNav />
      <main>
        <Hero stock={stock} />
        <ValueProps />
        <MarketPulseWidget pulse={pulse} />
        <PricingTable />
      </main>
      <LandingFooter />
    </div>
  );
}
