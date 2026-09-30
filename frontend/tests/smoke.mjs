import { chromium } from "playwright";

const baseUrl = process.env.SMOKE_BASE_URL ?? "http://127.0.0.1:3000";

async function requireVisible(page, selector, description) {
  const locator = page.locator(selector).first();
  await locator.waitFor({ state: "visible", timeout: 10_000 });
  if (!(await locator.isVisible())) {
    throw new Error(`${description} is not visible (${selector})`);
  }
}

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();

  await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded" });
  await requireVisible(page, 'input[type="email"]', "login email field");
  await requireVisible(page, 'input[type="password"]', "login password field");
  await requireVisible(page, 'button[type="submit"]', "login submit button");

  await page.goto(`${baseUrl}/forgot-password`, { waitUntil: "domcontentloaded" });
  await requireVisible(page, 'input[type="email"]', "forgot-password email field");
  await requireVisible(page, 'button[type="submit"]', "forgot-password submit button");

  await page.goto(`${baseUrl}/reset-password`, { waitUntil: "domcontentloaded" });
  await requireVisible(page, 'a[href="/forgot-password"]', "missing-token recovery link");

  // CI deliberately does not start FastAPI for the frontend job. The dashboard
  // must therefore render an explicit outage state rather than pretending the
  // failed requests mean that there are zero market signals.
  await page.goto(`${baseUrl}/dashboard`, { waitUntil: "domcontentloaded" });
  await requireVisible(page, '[data-testid="global-stock-search"] input', "global stock search");
  await requireVisible(page, '[data-testid="app-topbar"]', "app shell top bar");
  await requireVisible(page, '[data-testid="desktop-sidebar"]', "app shell sidebar");
  await requireVisible(page, '#app-content', "app shell content frame");
  await page.getByText("Some market data is temporarily unavailable:").waitFor({ state: "visible", timeout: 10_000 });
  await page.getByText("Signal data is temporarily unavailable.").waitFor({ state: "visible", timeout: 10_000 });

  // The intelligence dashboard follows the same production rule: an API outage
  // is shown explicitly and must never be interpreted as a bearish/neutral state.
  await page.goto(`${baseUrl}/market-pulse`, { waitUntil: "domcontentloaded" });
  await page.getByText("Market intelligence is temporarily unavailable.").waitFor({ state: "visible", timeout: 10_000 });
  await page.getByRole("heading", { name: "Live market context" }).waitFor({ state: "visible", timeout: 10_000 });
  await page.getByRole("heading", { name: "Artha stock screener" }).waitFor({ state: "visible", timeout: 10_000 });

  // Sector navigation must remain useful even when the backend is unavailable:
  // both the board and a direct sector-detail URL render explicit outage states.
  await page.goto(`${baseUrl}/sectors`, { waitUntil: "domcontentloaded" });
  await page.getByRole("heading", { name: "Sector intelligence" }).waitFor({ state: "visible", timeout: 10_000 });
  await page.getByText("Sector data is temporarily unavailable").waitFor({ state: "visible", timeout: 10_000 });

  await page.goto(`${baseUrl}/sectors/Banking`, { waitUntil: "domcontentloaded" });
  await page.getByRole("heading", { name: "Sector data is temporarily unavailable" }).waitFor({ state: "visible", timeout: 10_000 });
  await page.getByRole("link", { name: /Back to sectors/i }).waitFor({ state: "visible", timeout: 10_000 });

  // User-specific pages must remain protected without an access/refresh cookie.
  await page.goto(`${baseUrl}/portfolio`, { waitUntil: "domcontentloaded" });
  await page.waitForURL(/\/login\?next=%2Fportfolio|\/login\?next=\/portfolio/, { timeout: 10_000 });

  console.log("Frontend smoke checks passed");
} finally {
  await browser.close();
}
