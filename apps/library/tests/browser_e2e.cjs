/* Browser acceptance for the library shell and viewers (A19, A20, A27).
   Run through check_browser.py, which prepares the data and starts the server.
   Env: LIB_URL, PBI_DOC, ADF_DOC, IMPORT_FILE; CHROMIUM_PATH optional. */
const { chromium } = require("playwright");
const assert = require("assert");

const URL = process.env.LIB_URL;
const step = (name) => console.log("  ok " + name);

(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const page = await browser.newPage();
  globalThis.__browser = browser;
  const errors = [];
  globalThis.__errors = errors;
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error" && !/^Failed to load resource/.test(m.text())) errors.push(m.text()); });
  const failed = [];
  page.on("response", (r) => { if (r.status() >= 400) failed.push(`${r.status()} ${new globalThis.URL(r.url()).pathname}`); });

  // Home, type filter, search (A19)
  await page.goto(URL + "/#/");
  await page.getByRole("heading", { name: "All documentation" }).waitFor();
  assert.strictEqual(await page.locator("ol.documents > li").count(), 2);
  await page.getByRole("link", { name: "Power BI", exact: true }).click();
  await page.getByRole("heading", { name: "Power BI documentation" }).waitFor();
  assert.strictEqual(await page.locator("ol.documents > li").count(), 1);
  await page.getByRole("link", { name: "All documentation" }).click();
  await page.getByRole("heading", { name: "All documentation" }).waitFor();
  await page.getByLabel("Search documentation").fill("copy daily");
  await page.locator("ol.hits > li").first().waitFor();
  assert.match(await page.locator("ol.hits > li").first().innerText(), /Copy daily/);
  await page.getByLabel("Search documentation").fill("zzzz-no-such-thing");
  await page.getByText("No matching results").waitFor();
  step("browse, filter and search");

  // Power BI viewer: upstream producer, exact (A20)
  const pbiRev = await page.evaluate(async (id) => (await (await fetch(`/api/v1/documents/${id}`)).json()).current_revision_id, process.env.PBI_DOC);
  await page.goto(`${URL}/#/view/${process.env.PBI_DOC}/${pbiRev}`);
  const related = page.getByRole("complementary", { name: "Related documentation" });
  await related.getByText("Upstream: pipelines that write this").waitFor();
  assert.match(await related.innerText(), /Exact \(static evidence\)/);
  step("Power BI source shows its exact upstream pipeline");

  // Follow the link to the ADF activity: the viewer opens the exact activity (A20)
  await related.getByRole("link", { name: /Test factory|factory/i }).first().click();
  await page.waitForURL(/#\/view\/.+object=adf%3Aactivity%3APL_Load%2FCopy/);
  const adfFrame = page.frameLocator("iframe");
  await adfFrame.locator('details.act[open] summary', { hasText: "Copy daily" }).waitFor({ timeout: 10000 });
  assert.strictEqual(await page.locator(".viewer-status .notice").count(), 0, "no fallback notice");
  const back = page.getByRole("complementary", { name: "Related documentation" });
  await back.getByText("Downstream: reports that read this").waitFor();
  step("link opens the exact ADF activity, with the reverse link shown");

  // Back restores the Power BI view and panel
  await page.goBack();
  await page.waitForURL(new RegExp(`#/view/${process.env.PBI_DOC}/`));
  await page.getByRole("complementary", { name: "Related documentation" }).getByText("Upstream: pipelines that write this").waitFor();
  step("Back restores the origin");

  // Object selection inside the Power BI viewer: a measure
  await page.getByLabel("Select an object").selectOption({ label: "measure: Revenue" });
  await page.frameLocator("iframe").locator("details.measure[open] summary", { hasText: "Revenue" }).waitFor({ timeout: 10000 });
  step("selecting a measure opens it in the viewer");

  // Isolation (A27)
  const sandbox = await page.locator("iframe").getAttribute("sandbox");
  assert.strictEqual(sandbox, "allow-scripts");
  const frame = page.frames().find((f) => f !== page.mainFrame());
  const probe = await frame.evaluate(async () => {
    const out = {};
    try { out.parent = String(window.parent.document.title); } catch (e) { out.parent = "blocked"; }
    try { out.cookie = document.cookie; } catch (e) { out.cookie = "blocked"; }
    try { await fetch("/api/v1/documents"); out.fetch = "allowed"; } catch (e) { out.fetch = "blocked"; }
    try { window.top.location.href = "https://example.com/"; out.top = "navigated"; } catch (e) { out.top = "blocked"; }
    // A forged reply without the channel must be ignored by the shell.
    window.parent.postMessage({ protocol: "bi-doc-viewer", version: 1, type: "navigated", channel: "forged",
      revision_id: "x", object_id: "<img src=x onerror=alert(1)>", exact: false }, "*");
    return out;
  });
  assert.deepStrictEqual(probe, { parent: "blocked", cookie: "blocked", fetch: "blocked", top: "blocked" });
  await page.waitForTimeout(300);
  assert.strictEqual(await page.locator(".viewer-status .notice").count(), 0, "forged message ignored");
  assert.ok(page.url().startsWith(URL), "top window was not navigated");
  // A message from the shell's own window (not the frame) is ignored too.
  await page.evaluate(() => window.postMessage({ protocol: "bi-doc-viewer", version: 1, type: "viewer-ready", channel: "x",
    revision_id: "x", views: ["pbi.table"] }, "*"));
  step("viewer is isolated; forged messages are ignored");

  // Import with preview (publisher), then open the result
  await page.goto(URL + "/#/import");
  await page.getByLabel("Generated document (HTML)").setInputFiles(process.env.IMPORT_FILE);
  await page.getByText("New document", { exact: true }).waitFor();
  assert.strictEqual(await page.getByLabel("Include query code (M and SQL) in the shared library").isChecked(), false);
  await page.getByRole("button", { name: "Publish" }).click();
  await page.getByRole("link", { name: "Open it" }).click();
  await page.getByRole("complementary", { name: "Related documentation" }).waitFor();
  step("import with preview and open");

  // A legacy document (no manifest) is converted; its published viewer is read-only (A37).
  await page.goto(URL + "/#/import");
  await page.getByLabel("Generated document (HTML)").setInputFiles(process.env.LEGACY_FILE);
  await page.getByText("Older document (adf-doc-gen/2), converted").waitFor();
  await page.getByLabel("Title").fill("Legacy factory");
  await page.getByLabel("Title").dispatchEvent("change");
  await page.locator("#import-preview dd", { hasText: "Legacy factory" }).waitFor();
  await page.getByRole("button", { name: "Publish" }).click();
  await page.getByRole("link", { name: "Open it" }).click();
  const legacyFrame = page.frameLocator("iframe");
  await legacyFrame.getByText("Factory details", { exact: true }).first().click();
  await legacyFrame.getByText("This is a published, read-only copy.", { exact: false }).waitFor({ timeout: 10000 });
  assert.strictEqual(await legacyFrame.getByRole("button", { name: "Download updated HTML" }).count(), 0);
  assert.strictEqual(await legacyFrame.locator("#det-owner").count(), 0);
  step("legacy import converts; published viewer is read-only (A37)");

  const shellCsp = (await page.request.get(URL + "/")).headers()["content-security-policy"];
  assert.match(shellCsp, /script-src 'self'/);
  assert.deepStrictEqual(failed, []);
  // Only the isolation probe's own blocked attempts may appear.
  assert.deepStrictEqual(errors.filter((e) => !/Content Security Policy|Unsafe attempt to initiate navigation/.test(e)), []);
  step("no unexpected console errors");
  await browser.close();
})().catch(async (e) => {
  console.error(e);
  try { const p = (await (globalThis.__browser?.contexts()[0]?.pages() || []))[0];
    if (p) console.error("PAGE:", (await p.locator("main").innerText()).slice(0, 800)); } catch (_) {}
  console.error("ERRORS:", JSON.stringify(globalThis.__errors || []));
  process.exit(1);
});
