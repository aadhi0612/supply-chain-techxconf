# SiliconFlow UI

Two parts, both plain HTML/CSS/vanilla JS with **no framework and no build step**.

| Path | What it is |
|---|---|
| `index.html` | Landing page: a sticky aerial freight clip is scrubbed (never played) as you scroll while the product story rises over it, then clouds hand over to a text section (who it is for, one incident end to end). Open it directly or at `/ui/`. Add `?rec=1` for a deterministic recording driver (`window.__recTime(video, t)`). |
| `console/` | The operational console on top of the `/v1` API. ES modules, so serve it over HTTP (`siliconflow serve` serves it at `/ui/console/`). |
| `e2e/run.js` | Browser end-to-end test of the console (Playwright). |

## Console

Views: **Dashboard** (what is waiting on you, current plan, governance KPIs) · **Plan & orders** (what ships, arrivals, compliance
gate, tight pools; pick any option profile) · **Signals** (paste a supplier notice → grounded proposals → confirm; structured
event form; conflicts) · **Incidents** (impact if nobody acts, binding constraints, verified options with best/base/worst,
four-eyes approvals, execute, supplier confirm/decline, revert, re-plan, per-incident audit) · **Dataset** (data quality,
tables, snapshots + diff, JSON/CSV import) · **Audit & learning** (hash-chained audit log + verify, metrics, calibration,
record outcomes).

Design rules:
- All dynamic text is rendered through DOM text nodes (no `innerHTML`), so notices and supplier text cannot inject markup.
- The UI never offers an action the server will refuse (proposer cannot approve, one person one role, read-only roles), and
  surfaces server errors verbatim.
- Long analyses run in the background (`mode=async`) and the page polls the incident.
- API key lives in `sessionStorage` only.

### Run it
```bash
siliconflow serve --demo            # DEV ONLY: seeds the sample dataset and public demo users (sf_demo_pat, ...)
# open http://127.0.0.1:8000/  -> landing page -> "Open the console" -> sign in as any demo role
```
Without `--demo`, create a user with `siliconflow init-user` and paste its key on the sign-in screen.
Walk through the full loop with the role switcher in the header: report the notice as **Pat Planner**, select an option, approve as
Sam (planner), Priya, Fiona, Sasha and Carl, then execute and record the supplier's confirmation.

### End-to-end test
```bash
siliconflow serve --demo --port 8765     # fresh DB (SILICONFLOW_DB=sqlite:////tmp/x.db)
node ui/e2e/run.js http://127.0.0.1:8765 [screenshot-dir]   # needs `playwright` (Chromium)
```
It signs in, loads the plan and dataset, submits the example notice, acknowledges the ambiguity, waits for analysis, selects the
recommended option, collects every required approval as a different user, executes, records a supplier confirmation, checks the
viewer is read-only and the audit chain is intact, and fails on any JS error.

## Notes
- The landing page uses *illustrative personas*, deliberately not customer quotes: the product has no customers to quote yet.
- Fonts come from Google Fonts; the clip and poster come from the CDN URLs in `index.html`. The console works without them.
