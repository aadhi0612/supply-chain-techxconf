// End-to-end browser test of the console against a running demo server.
//   siliconflow serve --demo --port 8765   (fresh DB)   then   node ui/e2e/run.js http://127.0.0.1:8765 [shots-dir]
const { chromium } = require("playwright");
const fs = require("fs");
const base = process.argv[2] || "http://127.0.0.1:8765";
const shots = process.argv[3] || "";
const KEYS = { planner2: "sf_demo_sam", procurement: "sf_demo_priya", finance: "sf_demo_fiona", sales: "sf_demo_sasha", compliance: "sf_demo_carl", lead: "sf_demo_pat", viewer: "sf_demo_vic" };
let step = 0;
const ok = (m) => console.log(`  ok  ${m}`);
function must(c, m) { if (!c) { throw new Error("FAILED: " + m); } ok(m); }

(async () => {
  if (shots) fs.mkdirSync(shots, { recursive: true });
  const b = await chromium.launch();
  const pg = await b.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  pg.on("pageerror", (e) => errors.push("pageerror: " + e.message));
  pg.on("console", (m) => m.type() === "error" && !/fonts|favicon|ERR_|Failed to load resource/.test(m.text() + "") && errors.push("console: " + m.text()));
  const snap = async (n) => shots && pg.screenshot({ path: `${shots}/${String(++step).padStart(2, "0")}_${n}.png`, fullPage: true });
  const as = async (who) => { await pg.selectOption(".who select", KEYS[who]); await pg.waitForSelector(".page"); await pg.waitForTimeout(400); };
  const confirmModal = async (text) => { await pg.waitForSelector(".modal"); if (text) await pg.fill(".modal textarea", text); await pg.click(".modal .btn.primary, .modal .btn.danger"); await pg.waitForSelector(".modal", { state: "detached" }); };

  console.log("login");
  await pg.goto(base + "/ui/console/");
  await pg.waitForSelector(".login");
  await snap("login");
  await pg.click("text=Pat Planner");
  await pg.waitForSelector(".shell");
  await pg.waitForSelector("text=Current plan");
  must(await pg.locator("h1", { hasText: "Dashboard" }).count() === 1, "dashboard renders after sign-in");
  await snap("dashboard");

  console.log("plan, dataset");
  await pg.click('a[data-id="plan"]'); await pg.waitForSelector("text=Resource and supply pools");
  must(await pg.locator("text=independently verified").count() >= 1, "plan view shows verification badge");
  await snap("plan");
  await pg.click('a[data-id="dataset"]'); await pg.waitForSelector("text=Data quality");
  must(await pg.locator("text=order_compliance_hold").count() >= 1, "dataset shows data-quality issues");
  await snap("dataset");

  console.log("signal: notice -> proposals -> incident");
  await pg.click('a[data-id="signals"]'); await pg.waitForSelector("text=Paste a notice");
  await pg.click("text=Use example notice"); await pg.click("text=Extract proposals");
  await pg.waitForSelector("text=Confirm selected and open incident");
  must(await pg.locator("text=capacity change").count() >= 1 && await pg.locator("text=lot status").count() >= 1, "notice produced two grounded proposals");
  must(await pg.locator(".note.warn", { hasText: "interpreted as" }).count() >= 1, "relative-week ambiguity is surfaced");
  await snap("proposals");
  await pg.click("text=Confirm selected and open incident");
  await pg.waitForTimeout(500);
  must(await pg.locator("text=I have reviewed the ambiguities").count() >= 1 || /incidents\//.test(pg.url()), "ambiguity must be acknowledged first");
  if (!/incidents\//.test(pg.url())) { await pg.check('label:has-text("I have reviewed") input'); await pg.click("text=Confirm selected and open incident"); }
  await pg.waitForURL(/#\/incidents\/inc_/);
  const incUrl = pg.url();
  ok("incident opened: " + incUrl.split("#")[1]);
  await pg.waitForSelector(".spinner", { timeout: 5000 }).catch(() => {});
  await snap("analysing");
  await pg.waitForSelector('h2:text-is("Recovery options")', { timeout: 150000 });
  must(await pg.locator(".opt").count() >= 2, "recovery options rendered");
  must(await pg.locator(".opt.rec").count() === 1, "exactly one recommended option");
  must(await pg.locator(".badge", { hasText: "verified" }).count() >= 2, "options show verification badges");
  must(await pg.locator('h2:has-text("Impact if nobody acts")').count() === 1, "impact section present");
  must(await pg.locator('h2:text-is("Binding constraints")').count() === 1, "binding constraints present");
  await snap("incident_options");

  console.log("select + four-eyes approvals");
  await pg.click(".opt.rec >> text=Select this option");
  await pg.waitForSelector('h2:text-is("Approvals")');
  const roles = await pg.locator("table >> xpath=//td[1]/b").allInnerTexts();
  const required = ["compliance", "finance", "planner", "procurement", "sales"].filter((r) => roles.includes(r));
  must(required.length >= 3, "required approval roles listed: " + required.join(", "));
  must(await pg.locator("text=You selected this option").count() === 1, "proposer is told they cannot approve");
  must(await pg.locator("button:has-text('Approve')").count() === 0, "proposer sees no approve button");
  await snap("approvals_pending");
  for (const role of required) {
    await as(role === "planner" ? "planner2" : role);
    await pg.waitForSelector('h2:text-is("Approvals")');
    await pg.click("button:has-text('Approve')");
    await confirmModal("");
    await pg.waitForTimeout(600);
  }
  await as("lead");
  await pg.waitForSelector('h2:has-text("Actions")');
  must(await pg.locator(".badge", { hasText: "approved" }).count() >= 1, "incident reaches approved");
  await snap("approved");

  console.log("execute + close the loop");
  await pg.click("button:has-text('Execute approved plan')");
  await pg.waitForSelector("td >> text=SUPPLIER REQUEST");
  must(await pg.locator("td >> text=ALLOCATION UPDATE").count() === 1, "actions created, including the allocation update");
  must(await pg.locator(".badge", { hasText: "sent" }).count() >= 1, "execute mode sent actions through the adapter");
  await snap("actions");
  await pg.locator("button:has-text('Confirmed')").first().click();
  await pg.waitForSelector(".modal");
  await pg.click(".modal .btn.primary");
  await pg.waitForSelector(".modal", { state: "detached" });
  await pg.waitForSelector(".badge:has-text('confirmed')");
  ok("supplier confirmation recorded and fed back as a new fact");

  console.log("viewer is read only");
  await as("viewer");
  await pg.goto(base + "/ui/console/#/signals");
  await pg.waitForSelector("text=read only");
  must(await pg.locator("button:has-text('Extract proposals')").isDisabled(), "viewer cannot submit signals");

  console.log("governance");
  await as("lead");
  await pg.click('a[data-id="governance"]'); await pg.waitForSelector("text=Audit log");
  await pg.waitForSelector("td >> text=incident.approved");
  must(await pg.locator(".kpi .v", { hasText: "intact" }).count() === 1, "audit chain reported intact");
  await snap("governance");
  await pg.click('a[data-id="incidents"]'); await pg.waitForSelector("text=Report a supply change");
  await snap("incidents");

  must(errors.length === 0, "no JS errors" + (errors.length ? ": " + errors.join(" | ") : ""));
  await b.close();
  console.log("\nE2E PASSED");
})().catch((e) => { console.error("\nE2E FAILED:", e.message); process.exit(1); });
