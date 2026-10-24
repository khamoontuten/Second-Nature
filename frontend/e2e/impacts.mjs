// Acceptance test for the strategy impact analysis (brief Phase 12), driven through the real UI.
//
//   SN_E2E_URL   backend serving frontend/dist (default http://127.0.0.1:8000)
//   SN_E2E_OUT   screenshot/download directory (default e2e/screenshots)
//
// Every number checked in the UI is compared with the stored study result from the API, so the page is shown to
// display simulation outputs rather than fixed demonstration values. Exits non-zero on any failed step.
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const BASE = (process.env.SN_E2E_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const OUT = process.env.SN_E2E_OUT ?? path.join(path.dirname(new URL(import.meta.url).pathname), "screenshots");
mkdirSync(OUT, { recursive: true });

const results = [];
let failed = 0;
function check(cond, msg) {
  if (cond) console.log(`    ✓ ${msg}`);
  else { failed++; console.error(`    ✗ ${msg}`); }
  return cond;
}
async function step(n, title, fn) {
  console.log(`Step ${n}: ${title}`);
  try { await fn(); results.push({ n, title, ok: true }); }
  catch (e) { failed++; results.push({ n, title, ok: false, error: String(e) }); console.error(`    ✗ ${e.stack ?? e}`); }
}
async function api(p, opts = {}) {
  const r = await fetch(`${BASE}${p}`, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) throw new Error(`${opts.method ?? "GET"} ${p} → HTTP ${r.status}: ${await r.text()}`);
  return r.json();
}
const num = (s) => Number(String(s).replace(/[^0-9.\-−]/g, "").replace("−", "-"));
const close = (a, b, rel = 0.006, abs = 1.01) => Math.abs(a - b) <= Math.max(abs, rel * Math.abs(b));

async function latestStudy(pid, kind, after) {
  for (let i = 0; i < 600; i++) {
    const list = await api(`/api/projects/${pid}/impact-studies`);
    const s = list.find((x) => x.kind === kind && x.created_at > after);
    if (s && ["succeeded", "failed"].includes(s.status)) return api(`/api/impact-studies/${s.id}`);
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`no finished ${kind} study`);
}
const metric = (ev, key) => ev.metrics.find((m) => m.key === key);

// ---------------------------------------------------------------------------------------------------------------
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
const page = await context.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(`page error: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error" && (m.location()?.url ?? "").startsWith(BASE)) errors.push(`console: ${m.text()}`); });
page.on("response", (r) => { if (r.url().startsWith(`${BASE}/api/`) && r.status() >= 400) errors.push(`API ${r.status()} ${r.url()}`); });

let pid;
const studies = {};
const now = () => new Date().toISOString().replace("Z", "");
const shot = (name) => page.screenshot({ path: path.join(OUT, `impacts-${name}.png`), fullPage: true });
const results$ = () => page.locator('[data-testid="combination-results"]');
const tileValue = async (label) => num(await results$().getByRole("group", { name: label, exact: true }).locator("div").nth(1).innerText());
const builder = (id) => page.locator(`[data-builder="${id}"]`).getByRole("switch");

async function runCombination(expectText) {
  const t0 = now();
  await page.getByRole("button", { name: "Run combination" }).click();
  const st = await latestStudy(pid, "combination", t0);
  check(st.status === "succeeded", `combination study ${st.id.slice(0, 8)} succeeded (${st.result?.simulations_run} simulations)`);
  await results$().getByText(expectText).first().waitFor({ timeout: 30000 });
  return st;
}

await step(1, "Open the application", async () => {
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  check(await page.getByText("Second Nature").first().isVisible(), "project list loads");
});

await step(2, "Select / configure the baseline data center", async () => {
  const d = await api("/api/demo?site_id=ashburn_va&run=false", { method: "POST" });
  pid = d.project_id;
  await page.goto(`${BASE}/projects/${pid}/impacts`, { waitUntil: "networkidle" });
  await page.getByText("A · Baseline configuration").waitFor();
  const panel = await page.locator("#baseline").innerText();
  check(panel.includes("5 MW installed"), "baseline shows the 5 MW IT load");
  check(panel.includes("air cooled chiller crah"), "baseline shows the conventional cooling architecture");
  check(/NREL TMY3/.test(panel), "baseline shows the weather dataset");
});

await step(3, "Run the baseline simulation", async () => {
  const t0 = now();
  await page.getByRole("button", { name: "Run baseline" }).click();
  studies.baseline = await latestStudy(pid, "baseline", t0);
  check(studies.baseline.status === "succeeded", "baseline study succeeded");
  await page.getByTestId("baseline-source").getByText("baseline study").waitFor({ timeout: 20000 });
  const fac = metric(studies.baseline.result.baseline, "facility_mwh").value;
  const shown = num(await page.getByRole("group", { name: "Baseline facility electricity" }).locator("div").nth(1).innerText());
  check(close(shown, fac), `baseline facility electricity shown ${shown} = API ${fac.toFixed(1)} MWh`);
  await shot("1-baseline");
});

let stackFac, eaheFac;
await step(4, "Select one strategy (termite-mound stack ventilation)", async () => {
  await builder("stack").click();
  const sel = await page.locator("#builder").innerText();
  check(sel.includes("added as a dependency"), "full containment is added as a declared dependency");
});

await step(5, "Run it and inspect its individual impact", async () => {
  studies.stack = await runCombination("containment_full + stack");
  const ev = studies.stack.result.combination;
  stackFac = metric(ev, "facility_mwh");
  check(close(await tileValue("Facility electricity"), stackFac.value), `facility electricity tile = API ${stackFac.value.toFixed(0)} MWh`);
  check(close(await tileValue("Cooling electricity"), metric(ev, "cooling_mwh").value), "cooling electricity tile = API");
  check(stackFac.delta < 0 && stackFac.status === "modelled", `stack changes facility electricity by ${stackFac.delta.toFixed(0)} MWh (${stackFac.pct.toFixed(1)} %), modelled`);
  await shot("2-stack");
});

await step(6, "Return to baseline and select a different strategy (earth-air heat exchanger)", async () => {
  await page.getByRole("button", { name: "Reset to baseline" }).click();
  check((await page.locator("#builder").innerText()).includes("Baseline only"), "builder reset to baseline");
  await builder("eahe").click();
  studies.eahe = await runCombination("eahe");
  eaheFac = metric(studies.eahe.result.combination, "facility_mwh");
  check(close(await tileValue("Facility electricity"), eaheFac.value), `EAHE facility electricity tile = API ${eaheFac.value.toFixed(0)} MWh`);
});

await step(7, "Compare the second strategy's individual impact with the first", async () => {
  check(stackFac.delta !== eaheFac.delta, `stack ${stackFac.delta.toFixed(0)} MWh vs EAHE ${eaheFac.delta.toFixed(0)} MWh`);
  const t0 = now();
  await page.getByRole("button", { name: "Run individual impact study" }).click();
  studies.individual = await latestStudy(pid, "individual", t0);
  check(studies.individual.status === "succeeded", `individual study: ${studies.individual.result.individual.length} strategies, ${studies.individual.result.simulations_run} simulations`);
  const table = page.getByTestId("individual-table");
  await table.locator('[data-strategy="eahe"]').waitFor({ timeout: 30000 });
  for (const sid of ["stack", "eahe"]) {
    const ev = studies.individual.result.individual.find((e) => e.requested[0] === sid);
    const txt = await table.locator(`[data-strategy="${sid}"] [data-metric="facility_mwh"]`).innerText();
    check(close(num(txt.split("MWh")[0]), metric(ev, "facility_mwh").delta), `${sid} row facility change ${txt.split("\n")[0]} = API ${metric(ev, "facility_mwh").delta.toFixed(0)}`);
  }
  const same = studies.individual.result.individual.find((e) => e.requested[0] === "stack");
  check(close(metric(same, "facility_mwh").value, stackFac.value, 1e-9, 1e-6), "stack alone gives the same result in the individual study as in step 5 (deterministic)");
  await shot("3-individual");
});

let combo;
await step(8, "Select both strategies together", async () => {
  await builder("stack").click();
  const txt = await page.locator("#builder").innerText();
  check(txt.includes("Termite") && txt.includes("earth-air"), "both strategies selected");
  check(/Shared infrastructure/.test(txt), "shared outdoor-air intake / controls are flagged as counted once");
});

await step(9, "Run the combined simulation", async () => {
  studies.combo = await runCombination("containment_full + stack + eahe");
  combo = studies.combo.result;
  check(close(await tileValue("Facility electricity"), metric(combo.combination, "facility_mwh").value), "combined facility electricity tile = API");
});

await step(10, "Compare the combined result with both individual results", async () => {
  const row = combo.attribution.rows.find((r) => r.key === "facility_mwh");
  check(row.combined !== null && row.sum_individual !== null, `Σ individual ${row.sum_individual.toFixed(0)} MWh vs combined ${row.combined.toFixed(0)} MWh (non-additive ${row.non_additive.toFixed(0)})`);
  check(Math.abs(row.combined - (row.individual.stack + row.individual.eahe)) > 1, "combined result is simulated, not the sum of individual results");
  const t = await page.getByTestId("attribution-table").innerText();
  check(t.includes("stack alone") && t.includes("eahe alone") && t.includes("Σ individual"), "attribution table shows each strategy alone, the sum and the combination");
  await page.locator("#attribution").screenshot({ path: path.join(OUT, "impacts-4-attribution.png") });
});

await step(11, "Inspect energy, water, carbon, CAPEX and space estimates", async () => {
  const ev = combo.combination;
  for (const [label, key] of [["Water withdrawal", "water_withdrawal_m3"], ["Operational CO₂e", "co2e_t"], ["CAPEX", "capex"], ["Footprint at grade", "site_at_grade_m2"], ["Underground area", "underground_m2"]]) {
    const m = metric(ev, key);
    const shown = await results$().getByRole("group", { name: label, exact: true }).innerText();
    check(m.value !== null, `${label}: ${m.value?.toFixed?.(1)} ${m.unit} [${m.status}] — shown "${shown.split("\n").slice(1, 3).join(" ")}"`);
  }
  check(metric(ev, "capex").status === "estimated" && metric(ev, "capex").delta_range, "CAPEX is an estimate with a range");
  check(metric(ev, "underground_m2").status === "derived", "underground area is derived from the configured pipe field");
  check(ev.feasible, "combination meets all hard constraints");
  await shot("5-combination");
});

await step(12, "Change one supported physical parameter (EAHE pipe length 60 → 120 m) and re-run", async () => {
  const input = page.locator('[data-param="strategies.eahe.length_m"]');
  await input.fill("120");
  studies.param = await runCombination("length_m = 120");
});

await step(13, "Confirm the displayed outputs update", async () => {
  const before = metric(combo.combination, "underground_m2").value;
  const ev = studies.param.result.combination;
  const after = metric(ev, "underground_m2").value;
  check(close(after, 2 * before, 1e-9, 1e-6), `underground field ${before} → ${after} m² (doubles with pipe length)`);
  check(close(await tileValue("Underground area"), after), "underground tile shows the new value");
  check(close(await tileValue("Facility electricity"), metric(ev, "facility_mwh").value), "facility tile shows the re-run value");
  const capB = metric(combo.combination, "capex").delta, capA = metric(ev, "capex").delta;
  check(capA > capB, `incremental CAPEX increases with the longer pipes (${capB.toFixed(0)} → ${capA.toFixed(0)})`);
  const bases = Object.values(studies).map((s) => metric(s.result.baseline, "facility_mwh").value);
  check(bases.every((b) => b === bases[0]), "every study used the identical baseline (facility electricity)");
  const periods = Object.values(studies).map((s) => JSON.stringify(s.result.period));
  check(periods.every((p) => p === periods[0]), "every study used the identical simulation period");
});

await step(14, "Export the results with assumptions and status labels", async () => {
  const rowsel = page.getByTestId("export-table").locator("tr").nth(1);
  for (const fmt of ["CSV", "XLSX", "JSON"]) {
    const [dl] = await Promise.all([page.waitForEvent("download"), rowsel.getByRole("link", { name: fmt }).click()]);
    const file = path.join(OUT, dl.suggestedFilename());
    await dl.saveAs(file);
    const buf = readFileSync(file);
    if (fmt === "CSV") check(buf.toString().split("\n")[0].includes("status") && buf.toString().includes(",modelled,"), `CSV export has status labels (${file})`);
    if (fmt === "XLSX") check(buf.subarray(0, 2).toString() === "PK", `XLSX workbook saved (${file})`);
    if (fmt === "JSON") {
      const js = JSON.parse(buf.toString());
      check(js.limitations?.length > 0 && js.result.assumptions.unit_rates, `JSON export includes limitations and unit-rate assumptions (${file})`);
    }
  }
});

await step("+", "Pairwise interaction matrix and dark mode", async () => {
  await builder("radiative").click();
  const t0 = now();
  await page.getByRole("button", { name: "Pairwise interactions" }).click();
  const st = await latestStudy(pid, "pairwise", t0);
  check(st.status === "succeeded" && st.result.pairs.length === 3, `pairwise study: ${st.result.pairs.length} pairs simulated`);
  await page.getByTestId("pairs-table").waitFor();
  await page.locator("#attribution").screenshot({ path: path.join(OUT, "impacts-6-pairwise.png") });
  await page.getByLabel("Colour theme").selectOption("dark");
  await page.waitForTimeout(500);
  await shot("7-dark");
  await page.getByLabel("Colour theme").selectOption("light");
});

check(errors.length === 0, `no page/console/API errors${errors.length ? ": " + errors.slice(0, 5).join(" | ") : ""}`);
await browser.close();
console.log(`\n${results.filter((r) => r.ok).length}/${results.length} steps completed; ${failed} failed check(s). Screenshots and downloads: ${OUT}`);
process.exit(failed ? 1 : 0);
