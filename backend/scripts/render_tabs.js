// Browser check of every analysis tab, against the offline demo.
//
//   cd frontend && npm run build
//   cd ../backend && python scripts/demo_server.py --scripted-ai --client-app --port 8765
//   node scripts/render_tabs.js ../examples/clients.csv /tmp/shots      # needs Playwright (npm i -g playwright)
//
// Registers a demo organisation, uploads the CSV, runs an analysis, then opens every tab in headless Chromium,
// expands every collapsed panel, saves a screenshot per tab and fails on any page or console error.
// Set CIP_DEMO_URL to use another port.
const { chromium } = require("playwright");
const BASE = process.env.CIP_DEMO_URL || "http://localhost:8765";
const fs = require("fs");
const TABS = ["Pipeline", "Client", "Products & Services", "Security", "UX", "Competitors", "Pricing", "Apps", "Market",
  "Comparison", "Gaps", "Opportunities", "Cost & AI", "Sales", "Review", "Evidence", "Report"];

async function api(path, opts = {}, token) {
  const r = await fetch(BASE + path, { ...opts, headers: { ...(opts.headers || {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) } });
  const text = await r.text();
  if (!r.ok) throw new Error(`${path} ${r.status} ${text}`);
  return text ? JSON.parse(text) : null;
}

(async () => {
  const reg = await api("/api/auth/register", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ organization: "Demo Org", email: `demo${Date.now()}@example.com`, password: "s3cure-password!" }) });
  const token = reg.access_token;
  const form = new FormData();
  form.append("file", new Blob([fs.readFileSync(process.argv[2])], { type: "text/csv" }), "clients.csv");
  const up = await api("/api/uploads", { method: "POST", body: form }, token);
  const imp = await api(`/api/uploads/${up.id}/import`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }, token);
  const pid = imp.created_projects[0];
  const run = await api("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: pid, approve_gates: ["external_research", "repository_access", "client_report"] }) }, token);
  let status;
  for (let i = 0; i < 120; i++) {
    status = (await api(`/api/runs/${run.run_id}`, {}, token)).status;
    if (!["queued", "running"].includes(status)) break;
    await new Promise((r) => setTimeout(r, 1000));
  }
  console.log("run", run.run_id, status);

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
  await page.goto(BASE + "/");
  await page.evaluate((t) => localStorage.setItem("cip_token", t), token);
  await page.goto(`${BASE}/runs/${run.run_id}`);
  await page.waitForTimeout(1500);
  for (const tab of TABS) {
    const before = errors.length;
    await page.getByRole("button", { name: tab, exact: true }).first().click();
    await page.waitForTimeout(1200);
    // open every collapsed detail so nested panels render too
    await page.evaluate(() => document.querySelectorAll("details").forEach((d) => (d.open = true)));
    await page.waitForTimeout(300);
    const boundary = await page.locator("text=Something went wrong").count();
    const file = `${process.argv[3] || "."}/tab-${tab.replace(/[^a-z]/gi, "_")}.png`;
    await page.screenshot({ path: file, fullPage: true });
    console.log(`${tab}: ${errors.length - before} error(s)${boundary ? " + ERROR BOUNDARY" : ""}`);
  }
  console.log(errors.length ? errors.join("\n") : "no errors");
  if (errors.length) process.exitCode = 1;
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
