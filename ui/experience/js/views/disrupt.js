// The incident, end to end: notice -> proposals -> impact -> options -> four-eyes approvals -> actions -> supplier confirmation.
// Each step appends to the ledger and moves the plan stage that Track and Trace display.
import { NOTICE, PROPOSALS, IMPACT, OPTIONS, LEVERS, PEOPLE, actionsFor, requiredRoles } from "../data.js";
import { S, append, emit } from "../state.js";
import { h, fmt, badge, stateBadge, table, toast } from "../util.js";

const STEPS = ["Notice", "Proposed facts", "Impact", "Options", "Approvals", "Actions", "Supplier answers"];
const opt = () => OPTIONS.find((o) => o.id === S.option);
const go = (n) => { S.step = n; emit(); };
const PAT = "Pat Planner";

function card(i, title, body, action) {
  const done = S.step > i, now = S.step === i;
  return h("div", { class: `card step ${done ? "done" : now ? "now" : "todo"}` },
    h("h2", null, h("span", { class: "num" }, done ? "✓" : i + 1), title, h("span", { class: "grow" }), done ? badge("done", "ok") : now ? badge("your move", "warn") : null),
    done || now ? body : h("div", { class: "muted small" }, "Locked until the previous step is done."), now && action ? h("div", { style: "margin-top:12px" }, action) : null);
}

export default function disrupt(root) {
  const o = opt(), need = requiredRoles(o), got = S.approvals;
  root.append(
    h("div", { class: "page-head" }, h("div", null, h("h1", null, "Disrupt"), h("div", { class: "sub" },
      "Run the reference incident the way the platform does: a supplier email arrives, the system proposes facts, humans confirm, the engine shows impact and verified options, five roles approve, actions go out, suppliers answer. The numbers are the ones ",
      h("code", null, "siliconflow demo"), " prints on the bundled dataset."))),
    h("div", { class: "stepper" }, STEPS.map((s, i) => h("div", { class: `st ${S.step > i ? "done" : S.step === i ? "now" : ""}` }, `${i + 1}. ${s}`))),
    h("div", { class: "stack" },
      card(0, "Supplier notice arrives", h("div", null, h("pre", { class: "pay" }, NOTICE), h("div", { class: "muted small" }, "Unstructured email. The extractor may only propose; it cannot change plan state.")),
        h("button", { class: "btn primary", onclick: () => { append("system", "signal.received", { channel: "email", from: "OSAT-A", chars: NOTICE.length }); go(1); } }, "Receive notice")),
      card(1, "Proposed facts (needs a human)", h("div", null, table([{ label: "Proposal", render: (p) => h("div", null, h("b", null, p.summary), p.ambiguity ? h("div", { class: "small", style: "color:var(--warn)" }, "⚠ " + p.ambiguity) : null) },
          { label: "Type", key: "kind" }, { label: "Confidence", render: (p) => fmt.pct(p.conf * 100, 0) }, { label: "Status", render: () => stateBadge(S.step > 1 ? "confirmed" : "pending") }], PROPOSALS)),
        h("button", { class: "btn primary", onclick: () => {
          PROPOSALS.forEach((p) => append("system", "proposal.created", { id: p.id, kind: p.kind, confidence: p.conf }));
          append(PAT, "proposals.confirmed", { ids: PROPOSALS.map((p) => p.id), ambiguities_acknowledged: true });
          append("system", "snapshot.created", { version: 2 }); S.stage = "disrupted"; go(2); } }, "Acknowledge ambiguity & confirm as Pat")),
      card(2, "Impact if nobody acts", h("div", null,
          h("div", { class: "grid g3" }, h("div", null, h("div", { class: "muted small" }, "On-time units lost"), h("div", { class: "big" }, fmt.int(IMPACT.lost))),
            h("div", null, h("div", { class: "muted small" }, "Exposure"), h("div", { class: "big" }, fmt.usd(IMPACT.exposure))), h("div", null, h("div", { class: "muted small" }, "Plan, on-time"), h("div", { class: "big" }, `${fmt.int(IMPACT.base)} → ${fmt.int(IMPACT.after)}`))),
          h("h3", { style: "margin-top:12px" }, "Binding constraints (found by relaxing each tight pool and re-solving)"),
          table([{ label: "Pool", key: 0 }, { label: "Units recovered if relaxed", num: true, render: (r) => fmt.int(r[1]) }], IMPACT.binding.map((b) => b)),
          h("div", { class: "note", style: "margin-top:10px" }, "Open the Track tab: OSAT-A is flagged, LOT-S-77 is on hold, ORD-1002 and ORD-1003 lose units.")),
        h("button", { class: "btn primary", onclick: () => { append("system", "incident.analysed", { incident: "inc_493cf86f698e", lost_units: IMPACT.lost, exposure_usd: IMPACT.exposure }); go(3); } }, "See recovery options")),
      card(3, "Verified recovery options", h("div", { class: "grid g2" }, OPTIONS.map((x) => h("div", { class: `opt ${x.rec ? "rec" : ""} ${S.option === x.id ? "sel" : ""}` },
          h("div", { class: "row sb" }, h("b", null, x.name), x.rec ? badge("recommended", "info") : null), h("div", { class: "big" }, fmt.int(x.onTime), h("span", { class: "muted small" }, " on time")),
          h("div", { class: "kv" }, h("span", null, "Spend vs plan"), h("span", null, fmt.usd(x.spend)), h("span", null, "best / base / worst"), h("span", null, x.bbw.map(fmt.int).join(" / "))),
          x.levers.length ? h("ul", null, x.levers.map((l) => h("li", null, `${l}: ${LEVERS[l].text}`))) : h("ul", null, h("li", null, "No external levers")),
          x.live ? h("button", { class: "btn sm " + (S.option === x.id ? "dark" : ""), disabled: S.step !== 3, onclick: () => { S.option = x.id; emit(); } }, S.option === x.id ? "Selected" : "Select")
            : h("div", { class: "muted small" }, "Shown for comparison; the full console can execute any option.")))),
        h("button", { class: "btn primary", onclick: () => { append(PAT, "option.selected", { option: o.name, on_time: o.onTime, spend_usd: o.spend, levers: o.levers }); go(4); } }, `Propose "${o.name}" for approval`)),
      card(4, "Four-eyes approvals", h("div", null, h("div", { class: "muted small", style: "margin-bottom:8px" }, `Required roles: ${need.join(", ")}. The proposer cannot approve; one person approves one role.`),
          h("div", { class: "grid g3" }, PEOPLE.map((p) => { const mine = Object.entries(got).find(([, who]) => who === p.name);
            const ok = need.includes(p.role) && !got[p.role] && !p.proposer && !mine && S.step === 4;
            return h("div", { class: "card", style: "padding:12px" }, h("b", null, p.name), h("div", { class: "muted small" }, p.role + (p.proposer ? " · proposer" : "")),
              h("button", { class: "btn sm", style: "margin-top:8px", disabled: !ok, title: p.proposer ? "The proposer cannot approve their own plan" : "", onclick: () => { got[p.role] = p.name; append(p.name, "approval.granted", { role: p.role, option: o.name }); toast(`${p.name} approved as ${p.role}`); emit(); } },
                mine ? "Approved ✓" : p.proposer ? "Proposer" : need.includes(p.role) ? "Approve" : "Not required")); })),
          h("div", { class: "row", style: "margin-top:10px" }, need.map((r) => badge(`${r}${got[r] ? ": " + got[r].split(" ")[0] : ""}`, got[r] ? "ok" : "warn")))),
        h("button", { class: "btn primary", disabled: !need.every((r) => got[r]), onclick: () => { append("system", "incident.approved", { roles: need }); go(5); } }, need.every((r) => got[r]) ? "All approvals in: continue" : "Waiting for approvals")),
      card(5, "Actions go out", h("div", null, table([{ label: "Action", render: (a) => h("span", { class: "mono" }, a[0]) }, { label: "To", key: 1 }, { label: "Status", render: (a) => stateBadge(S.executed ? a[2] : "pending") }], actionsFor(o)),
          h("div", { class: "muted small", style: "margin-top:8px" }, "Idempotent: re-running does not re-send. Compliance reviews are drafted for humans, never sent automatically.")),
        h("button", { class: "btn primary", onclick: () => { actionsFor(o).forEach((a) => append("platform", "action." + a[2], { type: a[0], to: a[1] })); S.executed = true; go(6); } }, "Execute")),
      card(6, "Close the loop: suppliers answer", h("div", null, o.levers.length ? table([{ label: "Lever", render: (l) => h("div", null, h("b", { class: "mono" }, l), h("div", { class: "muted small" }, LEVERS[l].text)) }, { label: "Counterparty", render: (l) => LEVERS[l].cp },
            { label: "Answer", render: (l) => stateBadge(S.confirmed[l] ? "confirmed" : "pending") }], o.levers) : h("div", { class: "muted" }, "No external levers to confirm."),
          S.step > 6 ? h("div", { class: "note ok", style: "margin-top:10px" }, h("b", null, "Recovered: "), `${fmt.int(o.onTime)} units on time vs ${fmt.int(IMPACT.after)} if nobody acted. Check Track, Trace and the Ledger.`) : null),
        h("button", { class: "btn primary", onclick: () => { o.levers.forEach((l) => { S.confirmed[l] = true; append(LEVERS[l].cp, "supplier.confirmed", { lever: l }); }); append("system", "incident.completed", { on_time: o.onTime }); S.stage = "recovered"; go(7); } }, o.levers.length ? "Record supplier confirmations" : "Complete")),
      S.step >= 7 ? h("div", { class: "row" }, h("a", { class: "btn", href: "#/track" }, "See it in Track"), h("a", { class: "btn", href: "#/ledger" }, "Verify the ledger"), h("button", { class: "btn", onclick: () => { import("../state.js").then((m) => { m.reset(); m.emit(); }); } }, "Reset walkthrough")) : null));
}
