/* Browser acceptance for the generator desktop UI (served without a window).
   Run through check_desktop_browser.py. Env: APP_URL, FACTORY, PBIX, MISSING, OUT_DIR, JOBS, JOBS_DIR; CHROMIUM_PATH optional. */
const { chromium } = require("playwright");
const assert = require("assert");

const URL = process.env.APP_URL;
const step = (name) => console.log("  ok " + name);

(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error" && !/^Failed to load resource/.test(m.text())) errors.push(m.text()); });

  await page.goto(URL + "/#/prerequisites");
  await page.getByRole("heading", { name: "Prerequisites" }).waitFor();
  await page.getByText("never need Power BI Desktop or pbi-tools").waitFor();
  step("prerequisites per input type");

  // SQL Agent inputs read as words, not as the kind strings agent_script / agent_folder.
  await page.getByRole("cell", { name: "SQL Agent job script", exact: true }).waitFor();
  await page.getByRole("cell", { name: "SQL Agent job scripts (folder)", exact: true }).waitFor();
  assert.equal(await page.getByRole("cell", { name: /^agent_(script|folder)$/ }).count(), 0);
  await page.goto(URL + "/#/");
  // New batch leads with the task: one line on inputs, the full list of formats one click away; options that
  // matter only for shared output appear only when it is chosen; the rest wait under More options.
  await page.getByLabel("Inputs").waitFor();
  assert.equal(await page.locator("#inputs-help").getAttribute("open"), null);
  assert.equal(await page.getByText("A .pbip file alone is only a pointer", { exact: false }).isVisible(), false);
  await page.locator("#inputs-help > summary").click();
  await page.getByText("A .pbip file alone is only a pointer", { exact: false }).waitFor();
  assert.equal(await page.locator("#more-options").getAttribute("open"), null);
  await page.locator("#more-options > summary").click();
  assert.equal(await page.locator("#code-option").isVisible(), false, "local output keeps query code anyway");
  await page.getByLabel("Output", { exact: true }).selectOption("shared");
  assert.ok(await page.locator("#code-option").isVisible(), "shared output offers the query code choice");
  await page.getByLabel("Output", { exact: true }).selectOption("local");
  await page.locator("#more-options > summary").click();
  step("New batch leads with inputs, output and Review; format detail and options are one click away");
  await page.getByLabel("Inputs").fill([process.env.JOBS, process.env.JOBS_DIR].join("\n"));
  await page.getByLabel("Output folder").fill(process.env.OUT_DIR);
  await page.getByRole("button", { name: "Review inputs" }).click();
  await page.getByRole("cell", { name: "SQL Agent job script", exact: true }).waitFor();
  await page.getByRole("cell", { name: "SQL Agent job scripts (folder)", exact: true }).waitFor();
  step("SQL Agent inputs have readable labels in prerequisites and review");

  // A folder that is not itself a project is searched; the review lists what it holds, with paths that tell files apart.
  await page.goto(URL + "/#/");
  await page.getByLabel("Inputs").fill(process.env.FOLDER);
  await page.getByLabel("Output folder").fill(process.env.OUT_DIR);
  await page.getByRole("button", { name: "Review inputs" }).click();
  await page.locator("#scan-summary").waitFor();
  assert.match(await page.locator("#scan-summary").textContent(), /^Found 3 inputs in 1 selection \(\d+ folders searched\)\.$/);
  await page.getByText("Report Folder/Sub/Alpha.pbix", { exact: true }).waitFor();
  await page.getByText("Report Folder/Sub/Deeper/Beta.pbix", { exact: true }).waitFor();
  await page.getByText("Report Folder/Factory", { exact: true }).waitFor();
  await page.getByText("Generate queues exactly these inputs").waitFor();
  step("a folder is searched and its inputs are listed before anything runs");

  // The review belongs to the list it was made from: editing the inputs withdraws it, so an old Generate cannot queue it.
  await page.getByLabel("Inputs").fill(process.env.FOLDER + "-edited");
  await page.locator("#review-stale").waitFor();
  assert.equal(await page.getByRole("button", { name: /^Generate / }).count(), 0);
  step("changing the inputs withdraws the review");

  // ...and a review that is still running when the inputs change must not come back with a Generate button for the old list.
  await page.route("**/api/review", async (route) => { await new Promise((r) => setTimeout(r, 1500)); await route.continue(); });
  await page.getByLabel("Inputs").fill(process.env.FOLDER);
  await page.getByRole("button", { name: "Review inputs" }).click();
  await page.getByLabel("Inputs").fill(process.env.FOLDER + "-edited while the review ran");
  await page.locator("#review-stale").waitFor();
  await page.waitForTimeout(2600);                                          // the delayed response has now arrived
  assert.equal(await page.getByRole("button", { name: /^Generate / }).count(), 0);
  assert.equal(await page.locator("#scan-summary").count(), 0);
  assert.equal(await page.locator("#review-stale").count(), 1);
  await page.unroute("**/api/review");
  step("a review that was still running when the inputs changed is dropped");

  await page.goto(URL + "/#/");
  await page.getByLabel("Inputs").fill([process.env.FACTORY, process.env.MISSING, process.env.PBIX].join("\n"));
  await page.getByLabel("Output folder").fill(process.env.OUT_DIR);
  // The extra outputs are under More options, closed until one is chosen.
  assert.equal(await page.locator("#more-options").getAttribute("open"), null);
  await page.locator("#more-options > summary").click();
  await page.getByRole("group", { name: "Also write, beside each document" }).waitFor();
  await page.getByLabel("Agent context (.agent.md)").check();
  await page.getByRole("button", { name: "Review inputs" }).click();
  await page.getByRole("cell", { name: "Data Factory Git folder" }).waitFor();
  await page.getByText("not found", { exact: true }).waitFor();
  await page.getByText("PBIX generation is unavailable").waitFor();
  step("batch review shows each input before anything runs");

  await page.getByRole("button", { name: "Generate 2 of 3" }).click();
  await page.getByText("Finished: 1 of 3 succeeded.").waitFor({ timeout: 30000 });
  const states = await page.locator("td.state").allTextContents();
  assert.deepStrictEqual(states, ["Completed", "Failed", "Failed"]);
  step("per-item states; one success survives two failures");
  const written = require("fs").readdirSync(process.env.OUT_DIR);
  assert.ok(written.some((n) => n.endsWith(".agent.md")), "the agent context chosen in the form is beside the document: " + written);
  step("an extra output chosen in the form is written beside the document");

  await page.getByRole("button", { name: "Open" }).click();
  const frame = page.frameLocator("iframe.preview-frame");
  await frame.locator("h1").first().waitFor({ timeout: 10000 });
  assert.ok(await frame.getByText("Loads daily sales.").count() > 0);
  const sandbox = await page.locator("iframe.preview-frame").getAttribute("sandbox");
  assert.strictEqual(sandbox, "allow-scripts");
  const reachParent = await page.frames()[1].evaluate(() => { try { return String(window.parent.document.title); } catch (e) { return "blocked"; } });
  assert.strictEqual(reachParent, "blocked");
  step("document opens in an isolated preview");

  // Fix the missing input, then retry only that item.
  require("fs").cpSync(process.env.FACTORY, process.env.MISSING, { recursive: true });
  await page.locator("tr", { hasText: "missing-factory" }).getByRole("button", { name: "Retry" }).click();
  await page.getByText("Finished: 2 of 3 succeeded.").waitFor({ timeout: 30000 });
  step("failed item retried on its own");

  await page.getByRole("link", { name: "History" }).first().click();
  await page.getByText("2 of 3 succeeded").waitFor();
  step("history lists the batch");

  // Back on New batch, More options is open: an option in it (the agent context) is still chosen.
  await page.getByRole("link", { name: "New batch" }).first().click();
  await page.getByLabel("Inputs").waitFor();
  assert.notEqual(await page.locator("#more-options").getAttribute("open"), null);
  assert.ok(await page.getByLabel("Agent context (.agent.md)").isChecked());
  step("More options opens by itself while one of its options is chosen");

  // Library: not connected, so the connection form is the task (it is hidden only while connected).
  await page.goto(URL + "/#/library");
  await page.getByText("Not connected.").waitFor();
  assert.ok(await page.locator("#library-form").isVisible());
  step("Library shows the connection form while not connected");

  assert.deepStrictEqual(errors, []);
  step("no console errors");
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
