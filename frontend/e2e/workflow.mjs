// End-to-end smoke test of the Second Nature workbench in a real browser (Playwright + Chromium).
//
//   SN_E2E_URL   base URL of a running backend that serves frontend/dist (default http://127.0.0.1:8000)
//   SN_E2E_OUT   screenshot directory (default e2e/screenshots, git-ignored)
//
// Uses an existing project with a succeeded simulation, or creates the demonstration project via POST /api/demo.
// Exits non-zero on any page error, console error, failed API response or missing expected content.
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const BASE = (process.env.SN_E2E_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const OUT = process.env.SN_E2E_OUT ?? path.join(path.dirname(new URL(import.meta.url).pathname), "screenshots");
mkdirSync(OUT, { recursive: true });

const failures = [];
const fail = (msg) => { failures.push(msg); console.error(`  ✗ ${msg}`); };
const ok = (msg) => console.log(`  ✓ ${msg}`);

async function api(p, opts = {}) {
  const r = await fetch(`${BASE}${p}`, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) throw new Error(`${opts.method ?? "GET"} ${p} → HTTP ${r.status}: ${await r.text()}`);
  return r.json();
}

async function waitJob(jobId, timeoutS = 900) {
  const t0 = Date.now();
  for (;;) {
    const j = await api(`/api/jobs/${jobId}`);
    if (j.status === "succeeded") return j;
    if (j.status === "failed" || j.status === "cancelled") throw new Error(`job ${jobId} ${j.status}: ${j.error}`);
    if ((Date.now() - t0) / 1000 > timeoutS) throw new Error(`job ${jobId} timed out`);
    await new Promise((r) => setTimeout(r, 1000));
  }
}

async function demoProject() {
  const projects = await api("/api/projects");
  for (const p of projects) {
    const full = await api(`/api/projects/${p.id}`);
    const sim = full.simulations.find((s) => s.status === "succeeded" && s.scenarios.includes("S7"));
    if (sim) return { projectId: p.id, simId: sim.id };
  }
  console.log("No simulated project found — creating the demonstration project (this runs all scenarios).");
  const d = await api("/api/demo?site_id=ashburn_va", { method: "POST" });
  await waitJob(d.job_id);
  return { projectId: d.project_id, simId: d.simulation_id };
}

const { projectId, simId } = await demoProject();
console.log(`Project ${projectId}, simulation ${simId}`);

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
const page = await context.newPage();
page.on("pageerror", (e) => fail(`page error: ${e.message}`));
const external = new Set();
page.on("console", (m) => {
  if (m.type() !== "error") return;
  const src = m.location()?.url ?? "";
  // resources from other origins (e.g. OpenStreetMap map tiles) depend on the network, not on the application
  if (src && !src.startsWith(BASE)) external.add(new URL(src).host);
  else fail(`console error: ${m.text()}${src ? ` (${src})` : ""}`);
});
page.on("requestfailed", (r) => { if (!r.url().startsWith(BASE)) external.add(new URL(r.url()).host); });
page.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 400) fail(`API ${r.status()} ${r.url()}`); });

async function visit(slug, expectText, { charts = 0, shot = slug } = {}) {
  console.log(`• ${slug}`);
  await page.goto(`${BASE}/projects/${projectId}/${slug}?sim=${simId}`, { waitUntil: "networkidle" });
  try {
    await page.getByText(expectText, { exact: false }).first().waitFor({ timeout: 20000 });
    ok(`shows “${expectText}”`);
  } catch {
    fail(`${slug}: expected text “${expectText}” not found`);
  }
  await page.waitForTimeout(800); // let ECharts finish its entry animation before the screenshot
  if (charts) {
    const n = await page.locator("[data-chart-id] canvas").count();
    if (n >= charts) ok(`${n} rendered charts (≥ ${charts})`);
    else fail(`${slug}: only ${n} rendered charts, expected ≥ ${charts}`);
  }
  await page.screenshot({ path: path.join(OUT, `${shot}.png`), fullPage: true });
}

// Project list
console.log("• projects");
await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
await page.getByText("Second Nature").first().waitFor();
await page.screenshot({ path: path.join(OUT, "projects.png"), fullPage: true });
ok("project list renders");

await visit("setup", "Climate data");
await visit("climate", "Hourly", { charts: 6 });
await visit("free-cooling", "Free-cooling", { charts: 3 });
await visit("strategies", "Biomimetic strategy explorer", { charts: 2 });

// Calculator: change a slider with the keyboard and check that the result updates
await visit("calculator", "Chimney height versus natural airflow", { charts: 3 });
{
  const out = page.locator("dd").filter({ hasText: "g/kg_da" }).first();
  const before = await out.textContent();
  const slider = page.getByRole("slider", { name: "Relative humidity" });
  await slider.focus();
  for (let i = 0; i < 10; i++) await page.keyboard.press("ArrowRight");
  await page.waitForTimeout(1200);
  const after = await out.textContent();
  if (before !== after) ok(`psychrometric result updated (${before} → ${after})`);
  else fail("calculator: humidity ratio did not change after moving the RH slider");
}

// Comparison: charts + table toggle
await visit("compare", "Annual PUE by scenario", { charts: 10 });
{
  const card = page.locator('[data-chart-id="c21-pue"]');
  await card.getByRole("button", { name: "Table" }).click();
  const rows = await card.locator("tbody tr").count();
  if (rows >= 8) ok(`PUE table view lists ${rows} scenarios`);
  else fail(`compare: PUE table view has ${rows} rows`);
  await card.getByRole("button", { name: "Chart" }).click();
}

await visit("optimize", "Recommended configuration", { charts: 0 });

// Report: generate the JSON export and reproduce S0
await visit("report", "Executive summary");
{
  const card = page.locator(".card").filter({ hasText: "Complete machine-readable results" });
  await card.getByRole("button", { name: "Generate" }).click();
  try {
    await page.getByRole("link", { name: "Download" }).first().waitFor({ timeout: 120000 });
    ok("JSON report generated and download link shown");
  } catch {
    fail("report: no download link after generating JSON");
  }
  await page.getByRole("button", { name: "Reproduce S0" }).click();
  try {
    await page.getByText("Reproduced:", { exact: false }).first().waitFor({ timeout: 120000 });
    ok("S0 reproduced from stored inputs (identical hourly results)");
  } catch {
    fail("report: reproduction did not report identical results");
  }
  await page.screenshot({ path: path.join(OUT, "report-after.png"), fullPage: true });
}

// Dark mode is a selected palette, not a flip: check the token changes and capture the comparison page
await page.getByLabel("Colour theme").selectOption("dark");
await page.waitForTimeout(300);
const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
if (bg && bg !== "rgb(255, 255, 255)") ok(`dark theme applied (body ${bg})`);
else fail(`dark theme not applied (body ${bg})`);
await visit("compare", "Annual PUE by scenario", { charts: 10, shot: "compare-dark" });
await visit("calculator", "Chimney height versus natural airflow", { charts: 3, shot: "calculator-dark" });
await page.getByLabel("Colour theme").selectOption("light");

await browser.close();
if (external.size) console.log(`\nNote: external resources could not be loaded from: ${[...external].join(", ")} (network policy; the map shows no tiles).`);
console.log(failures.length ? `\n${failures.length} failure(s)` : "\nAll E2E checks passed.");
console.log(`Screenshots: ${OUT}`);
process.exit(failures.length ? 1 : 0);
