// Browser check of the Upload page: the manual client form (invalid values, save, confirmation) and the CSV review
// (a missing website filled in, duplicates decided, an existing client merged field by field, import, result).
//
//   cd frontend && npm run build
//   cd ../backend && python scripts/demo_server.py --port 8765 &
//   node scripts/check_intake_ui.js /tmp/intake-shots     # needs Playwright; screenshots go to that folder
//
// Exits non-zero on any page or console error. Set CIP_DEMO_URL to use another port.
const { chromium } = require("playwright");
const fs = require("fs");
const BASE = process.env.CIP_DEMO_URL || "http://localhost:8765";
const OUT = process.argv[2];
const CSV = `Client Name,Client ID,Client Email,Project Name,Website URL,Industry
Acme Robotics,ACM-1,ops@acme-robotics.io,Portal,https://acme-robotics.io,Robotics
Beta Shop,,,Store,,Retail
Delta Ltd,,,App,https://delta.io,Fintech
Delta,,,App,https://delta-app.com,Banking
Zeta Labs Inc,ZL-9,,Zeta,https://www.zeta-labs.com,Machine learning
Acme Robotics,ACM-1,ops@acme-robotics.io,Portal,https://acme-robotics.io/,Robotics
`;
(async () => {
  const reg = await (await fetch(BASE + "/api/auth/register", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ organization: "Demo Org", email: `demo${Date.now()}@example.com`, password: "s3cure-password!" }) })).json();
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
  page.on("dialog", (d) => d.accept());
  await page.goto(BASE + "/");
  await page.evaluate((t) => localStorage.setItem("cip_token", t), reg.access_token);
  const shot = async (n) => page.screenshot({ path: `${OUT}/${n}.png`, fullPage: true });

  // ---- manual entry
  await page.goto(BASE + "/upload?tab=manual");
  await page.waitForTimeout(800);
  await page.fill("#manual-client_name", "Zeta Labs");
  await page.fill("#manual-project_url", "zeta-labs");
  await page.fill("#manual-client_email", "info@");
  await page.getByRole("button", { name: "Check and save…" }).click();
  await page.waitForTimeout(600);
  await shot("m1-invalid");
  await page.fill("#manual-project_url", "zeta-labs.com");
  await page.fill("#manual-client_email", "info@zeta-labs.com");
  await page.fill("#manual-industry", "AI");
  await page.getByRole("button", { name: "Check and save…" }).click();
  await page.waitForTimeout(600);
  await shot("m2-confirm");
  await page.getByRole("button", { name: "Confirm and save" }).click();
  await page.waitForTimeout(800);
  await shot("m3-saved");
  console.log("manual saved:", await page.locator("text=Client saved").count());

  // ---- csv
  fs.writeFileSync(`${OUT}/clients.csv`, CSV);
  await page.goto(BASE + "/upload?tab=csv");
  await page.waitForTimeout(600);
  await page.setInputFiles('input[type="file"]', `${OUT}/clients.csv`);
  await page.getByRole("button", { name: "Upload and review" }).click();
  await page.waitForTimeout(1500);
  await shot("c1-review");
  // fix the missing website
  await page.getByRole("button", { name: "Fill in / correct" }).first().click();
  await page.fill("#row3-project_url", "beta-shop.com");
  await page.getByRole("button", { name: "Validate correction" }).click();
  await page.waitForTimeout(800);
  // duplicates: add row 2, skip row 7, add both Deltas
  for (const [row, a] of [[2, "Add"], [7, "Skip"], [4, "Add"], [5, "Add"]]) {
    await page.locator(`input[name="dup-${row}"]`).nth(a === "Add" ? 0 : 1).click();
    await page.waitForTimeout(500);
  }
  // existing match (Zeta): merge, choose values
  await page.getByRole("button", { name: "Merge", exact: true }).first().click();
  await page.waitForTimeout(600);
  const radios = page.locator('input[name^="merge-6-"]');
  const n = await radios.count();
  for (let i = 0; i < n; i += 2) { await radios.nth(i + 1).click(); await page.waitForTimeout(300); }
  await page.waitForTimeout(500);
  await shot("c2-decided");
  await page.getByRole("button", { name: "Review and confirm import…" }).click();
  await page.waitForTimeout(400);
  await shot("c3-confirm");
  await page.getByRole("button", { name: "Confirm and import" }).click();
  await page.waitForTimeout(1500);
  await shot("c4-result");
  console.log("result card:", await page.locator("text=7. Result").count());
  console.log(errors.length ? errors.join("\n") : "no errors");
  if (errors.length) process.exitCode = 1;
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
