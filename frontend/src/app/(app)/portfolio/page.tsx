import { redirect } from "next/navigation";
import { PortfolioClient } from "@/components/portfolio/portfolio-client";
import { getPortfolioSummary } from "@/lib/portfolio-data";

export default async function PortfolioPage() {
  const { summary, unauthorized } = await getPortfolioSummary();

  if (unauthorized || !summary) {
    redirect("/login?next=/portfolio");
  }

  return <PortfolioClient initialSummary={summary} />;
}
