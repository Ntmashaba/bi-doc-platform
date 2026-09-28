/* A12/A38: the portable export works from file:// with the network off.
   Run through check_offline_browser.py. Env: EXPORT_DIR, PARTIAL_DIR, ADF_ID, PBI_ID; CHROMIUM_PATH or BROWSER_CHANNEL. */
const { chromium } = require("playwright");
const assert = require("assert");
const path = require("path");
const { pathToFileURL } = require("url");

const step = (name) => console.log("  ok " + name);
const index = (dir) => pathToFileURL(path.join(dir, "index.html")).href;

(async () => {
  const opts = process.env.BROWSER_CHANNEL ? { channel: process.env.BROWSER_CHANNEL }
    : process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {};
  const browser = await chromium.launch(opts);
  const context = await browser.newContext();
  await context.setOffline(true);
  const network = [];
  context.on("request", (r) => { if (!r.url().startsWith("file:")) network.push(r.url()); });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

  await page.goto(index(process.env.EXPORT_DIR));
  await page.getByRole("heading", { name: "BI documentation" }).waitFor();
  await page.getByLabel("Search this export").fill("revenue");
  await page.getByRole("link", { name: /Sales model/ }).first().waitFor();
  await page.getByLabel("Search this export").fill("copy daily");
  await page.getByRole("link", { name: /^f/ }).first().waitFor();
  step("catalogue and search work offline");

  // ADF activity -> related Power BI source -> Back (A38)
  await page.goto(index(process.env.EXPORT_DIR) + `#/doc/${process.env.ADF_ID}?object=${encodeURIComponent("adf:activity:PL_Load/Copy daily")}`);
  await page.locator("#nav-status", { hasText: "Showing Copy daily" }).waitFor({ timeout: 15000 });
  const link = page.getByRole("complementary", { name: "Related documentation" }).getByRole("link").first();
  await link.click();
  await page.getByRole("heading", { name: "Sales model" }).waitFor();
  await page.locator("#nav-status", { hasText: "Showing" }).waitFor({ timeout: 15000 });
  const reverse = page.getByRole("complementary", { name: "Related documentation" });
  await reverse.getByText("Used by").waitFor().catch(async () => { await reverse.getByText("Uses or feeds").waitFor(); });
  await page.evaluate(() => history.back());                // what the Back button does
  await page.getByRole("heading", { name: "factory", exact: true }).waitFor();
  await page.locator("#nav-status", { hasText: "Showing Copy daily" }).waitFor({ timeout: 15000 });
  step("object -> object -> Back inside the viewer");

  const frame = page.frames().find((f) => f !== page.mainFrame());
  const probe = await frame.evaluate(() => { try { return String(window.parent.document.title); } catch (e) { return "blocked"; } });
  assert.strictEqual(probe, "blocked");
  step("documents stay sandboxed from the index page");

  await page.goto(index(process.env.PARTIAL_DIR) + `#/doc/${process.env.ADF_ID}`);
  await page.getByText("Not included in this export", { exact: false }).first().waitFor();
  step("links outside the export say Not included");

  assert.deepStrictEqual(network, [], "network requests: " + network.join(", "));
  assert.deepStrictEqual(errors, []);
  step("zero network requests and no console errors");
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
