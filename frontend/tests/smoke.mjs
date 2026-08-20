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

  console.log("Frontend smoke checks passed");
} finally {
  await browser.close();
}
