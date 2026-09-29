"use strict";
/* Standalone FTZ UI. All DOM is built with textContent (no innerHTML) so stored data can never inject markup. */

let L = {};            // lookups
const $ = (s, r = document) => r.querySelector(s);
const view = $("#view");

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "value") el.value = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}

let ME = null;         // signed-in user
const isAdmin = () => ME && ME.role === "admin" && !ME.is_demo;

async function api(method, url, body) {
  const r = await fetch(url, {
    method, headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({ errors: ["unexpected response"] }));
  if (r.status === 401 && url !== "/auth/login") { location.href = "/"; throw Object.assign(new Error("api"), { errors: ["Signed out - redirecting to sign in"] }); }
  if (!r.ok) throw Object.assign(new Error("api"), { errors: data.errors || ["request failed"] });
  return data;
}
const get = (u) => api("GET", u), post = (u, b) => api("POST", u, b || {});

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2600);
}
const money = (n) => n == null ? "" : Number(n).toLocaleString(undefined, { style: "currency", currency: "USD" });
const qty = (n) => n == null ? "" : Number(n).toLocaleString(undefined, { maximumFractionDigits: 4 });
const today = () => new Date().toISOString().slice(0, 10);
const partyName = (id) => (L.parties.find((p) => p.id == id) || {}).name || "";
const opts = (list, blank = true) => [...(blank ? [["", "—"]] : []), ...list];
const partyOpts = (kind) => opts(L.parties.filter((p) => !kind || p.kind === kind).map((p) => [p.id, p.name]));
const zoneOpts = () => L.zones.map((z) => [z.id, `${z.zone_no} — ${z.name}`]);

const TAGS = { approved: "ok", active: "ok", closed: "ok", returned: "ok", done: "ok", submitted: "warn", issued: "warn", in_transit: "warn",
  arrived: "warn", open: "warn", draft: "", rejected: "bad", revoked: "bad", expired: "bad", cancelled: "bad" };
const tag = (s, extra) => h("span", { class: "tag " + (extra || TAGS[s] || "") }, String(s).replace("_", " "));

function table(cols, data, onClick, empty = "Nothing here yet.") {
  if (!data.length) return h("div", { class: "empty" }, empty);
  return h("table", {},
    h("tr", {}, cols.map((c) => h("th", { class: c.num ? "num" : "" }, c.h))),
    data.map((row) => h("tr", { class: onClick ? "click" : "", onclick: onClick && (() => onClick(row)) },
      cols.map((c) => h("td", { class: c.num ? "num" : "" }, c.f ? c.f(row) : row[c.k])))));
}

/* ---------- generic forms ---------- */
function field(f, val) {
  let input;
  if (f.type === "select") {
    input = h("select", { name: f.name }, f.options.map(([v, t]) => h("option", { value: v }, t)));
    input.value = val ?? "";
  } else if (f.type === "textarea") input = h("textarea", { name: f.name }, val ?? "");
  else if (f.type === "checks") {
    input = h("div", { class: "checks", "data-name": f.name },
      f.options.map(([v, t]) => h("label", {}, h("input", { type: "checkbox", value: v, checked: (val || []).includes(v) }), t)));
  } else if (f.type === "checkbox") input = h("input", { type: "checkbox", name: f.name, checked: !!val, style: "width:auto" });
  else input = h("input", { name: f.name, type: f.type || "text", step: f.type === "number" ? "any" : null, value: val ?? "" });
  return h("label", { class: f.wide ? "wide" : "" }, f.label, f.required ? h("span", { class: "req" }, " *") : "", input);
}
function readFields(root, fields) {
  const out = {};
  for (const f of fields) {
    if (f.type === "checks") out[f.name] = [...$(`[data-name="${f.name}"]`, root).querySelectorAll("input:checked")].map((i) => i.value);
    else if (f.type === "checkbox") out[f.name] = $(`[name="${f.name}"]`, root).checked;
    else out[f.name] = $(`[name="${f.name}"]`, root).value;
  }
  return out;
}
function showErrors(box, e) {
  box.replaceChildren(...(e.errors ? [h("ul", { class: "errs" }, e.errors.map((m) => h("li", {}, m)))] : [h("div", { class: "errs" }, String(e))]));
}

function modal(title, body, { narrow = false } = {}) {
  const close = () => wrap.remove();
  const wrap = h("div", { class: "modal" + (narrow ? " narrow" : ""), onmousedown: (ev) => ev.target === wrap && close() },
    h("div", {}, h("div", { class: "bar" }, h("h1", { class: "grow" }, title), h("button", { onclick: close }, "Close")), body));
  document.body.append(wrap);
  return close;
}

document.addEventListener("keydown", (e) => { if (e.key === "Escape") { const m = document.querySelectorAll(".modal"); if (m.length) m[m.length - 1].remove(); } });

function formModal(title, fields, values, submitLabel, onSubmit) {
  const errBox = h("div");
  const form = h("form", {}, h("div", { class: "fields" }, fields.map((f) => field(f, (values || {})[f.name]))), errBox,
    h("div", { class: "bar" }, h("button", { class: "primary", type: "submit" }, submitLabel)));
  const close = modal(title, form, { narrow: fields.length < 7 });
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try { await onSubmit(readFields(form, fields)); close(); } catch (e) { if (e.errors) showErrors(errBox, e); else throw e; }
  });
}

/* Editable grid of merchandise lines. */
function linesEditor(cols, initial) {
  const body = h("tbody");
  const addRow = (vals = {}) => {
    const tr = h("tr", {}, cols.map((c) => {
      const el = c.type === "select" ? h("select", { "data-k": c.k }, c.options.map(([v, t]) => h("option", { value: v }, t)))
        : h("input", { "data-k": c.k, type: c.type || "text", step: c.type === "number" ? "any" : null });
      el.value = vals[c.k] ?? c.def ?? "";
      return h("td", {}, el);
    }), h("td", {}, h("button", { type: "button", class: "sm", onclick: () => tr.remove() }, "✕")));
    body.append(tr);
  };
  (initial && initial.length ? initial : [{}]).forEach(addRow);
  const el = h("div", { class: "lines" },
    h("table", {}, h("thead", {}, h("tr", {}, cols.map((c) => h("th", {}, c.h)), h("th"))), body),
    h("button", { type: "button", class: "sm", onclick: () => addRow() }, "+ Add line"));
  el.read = () => [...body.children].map((tr) => Object.fromEntries([...tr.querySelectorAll("[data-k]")].map((i) => [i.dataset.k, i.value])));
  return el;
}
const STATUS_OPTS = () => Object.entries(L.zone_statuses).map(([k, v]) => [k, `${k} — ${v}`]);
const MODE_OPTS = () => opts(L.modes.map((m) => [m, m]));

/* ---------- routing ---------- */
const NAV = [
  ["dashboard", "Dashboard"], ["sep", "Foreign-Trade Zone"], ["admissions", "e214 Admissions"], ["permits", "e216 Permits & Activity"],
  ["inventory", "Zone Inventory"], ["sep", "Bonded movements"], ["inbonds", "In-Bond IT · TE · IE"], ["sep", "System"],
  ["reports", "Reports"], ["setup", "Setup"], ["audit", "Audit & Export"], ["sep", "Account"], ["account", "My Account"], ["users", "Users"],
];
const VIEWS = {};
async function go(name, arg) {
  location.hash = name;
  document.querySelectorAll("#menu a").forEach((a) => a.classList.toggle("on", a.dataset.v === name));
  view.replaceChildren(h("div", { class: "empty" }, "Loading…"));
  try { L = await get("/api/lookups"); view.replaceChildren(await VIEWS[name](arg)); }
  catch (e) { view.replaceChildren(h("div", { class: "errs" }, (e.errors || [String(e)]).join("; "))); }
}
const refresh = () => go((location.hash || "#dashboard").slice(1));
async function act(fn, msg) {   // run an action, toast result, or show its errors
  try { const r = await fn(); if (msg) toast(msg); return r; }
  catch (e) { if (e.errors) { alert(e.errors.join("\n")); return null; } throw e; }
}
function buildShell() {
  $("#menu").append(...NAV.filter(([k]) => k !== "users" || isAdmin()).map(([k, t]) => k === "sep" ? h("div", { class: "sep" }, t) : h("a", { "data-v": k, onclick: () => go(k) }, t)));
  $("#who").replaceChildren(h("div", { class: "who" }, "Signed in as ", h("b", {}, ME.username), ME.is_demo ? " (demo)" : ""),
    h("button", { class: "sm signout", onclick: async () => { try { await post("/auth/logout"); } finally { location.href = "/"; } } }, "Sign out"));
}

const head = (t, sub, ...btns) => h("div", {}, h("div", { class: "bar" }, h("h1", { class: "grow" }, t), btns), sub && h("p", { class: "sub" }, sub));
const kv = (pairs) => h("div", { class: "kv" }, pairs.map(([k, v]) => h("div", {}, h("span", {}, k), v == null || v === "" ? "—" : v)));

/* ---------- dashboard ---------- */
VIEWS.dashboard = async () => {
  const d = await get("/api/dashboard");
  const n = (o, k) => o[k] || 0;
  const stat = (v, t, go_) => h("div", { class: "stat", style: go_ ? "cursor:pointer" : "", onclick: go_ && (() => go(go_)) }, h("b", {}, v), h("span", {}, t));
  const inv = Object.fromEntries(d.inventory.map((i) => [i.zone_status, i]));
  return h("div", {},
    head("Dashboard", "Compliance position at a glance."),
    h("div", { class: "grid" },
      stat(n(d.admissions, "submitted"), "e214 awaiting CBP approval", "admissions"),
      stat(n(d.admissions, "draft"), "e214 drafts", "admissions"),
      stat(d.permits_active, "active e216 permits", "permits"),
      stat(d.inbond_open.IT, "open IT (61)", "inbonds"), stat(d.inbond_open.TE, "open TE (62)", "inbonds"), stat(d.inbond_open.IE, "open IE (63)", "inbonds"),
      ["PF", "NPF", "D", "ZR"].map((s) => stat(money((inv[s] || {}).value || 0), `${s} inventory value`, "inventory"))),
    h("h2", {}, "Needs attention"),
    h("div", { class: "card" },
      d.inbond_overdue.map((b) => h("div", { class: "alert bad" }, `In-bond ${b.doc_no} (${b.type}) was due at ${b.dest_port} on ${b.due_date} and has not arrived.`)),
      d.inbond_unclosed_arrived.map((b) => h("div", { class: "alert" }, `In-bond ${b.doc_no} (${b.type}) arrived ${b.arrival_date} but is not closed — ${b.type === "IT" ? "enter the consumption entry" : "record proof of export"}.`)),
      d.removals_overdue.map((a) => h("div", { class: "alert bad" }, `Temporary removal ${a.act_no} (${a.lot_no}) was due back ${a.expected_return}.`)),
      d.permits_expiring.map((p) => h("div", { class: "alert" }, `e216 ${p.permit_no} expires ${p.valid_to}.`)),
      d.submitted_admissions.map((a) => h("div", { class: "alert" }, `e214 ${a.doc_no} is submitted — record the CBP approval or rejection.`)),
      !d.inbond_overdue.length && !d.inbond_unclosed_arrived.length && !d.removals_overdue.length && !d.permits_expiring.length && !d.submitted_admissions.length
        ? h("div", { class: "empty" }, "Nothing outstanding.") : null));
};

/* ---------- e214 ---------- */
const LINE_COLS = () => [
  { k: "description", h: "Description" }, { k: "htsus", h: "HTSUS" }, { k: "coo", h: "COO" },
  { k: "qty", h: "Qty", type: "number" }, { k: "uom", h: "UOM", def: "PCS" }, { k: "value", h: "Value USD", type: "number" },
  { k: "zone_status", h: "Status", type: "select", options: STATUS_OPTS(), def: "PF" },
  { k: "duty_rate", h: "PF duty %", type: "number" }, { k: "location", h: "Location" },
];

VIEWS.admissions = async () => {
  const list = await get("/api/admissions");
  return h("div", {},
    head("e214 — FTZ Admission & Status Designation",
      "Base form 214 with 214B continuation sheets; tick Census statistics to use 214A / 214C. Each line carries its own status: PF, NPF, D or ZR.",
      h("button", { class: "primary", onclick: () => admissionForm() }, "+ New e214")),
    h("div", { class: "card" }, table([
      { h: "Document", k: "doc_no" }, { h: "Status", f: (r) => tag(r.status) }, { h: "Zone", k: "zone_no" },
      { h: "Carrier", k: "carrier_name" }, { h: "B/L / AWB", k: "transport_doc" }, { h: "Admitted", k: "entry_date" },
      { h: "Lines", k: "line_count", num: true }, { h: "Value", f: (r) => money(r.total_value), num: true },
    ], list, (r) => admissionDetail(r.id), "No e214 applications yet.")));
};

function admissionForm(existing) {
  const f = [
    { name: "zone_id", label: "Zone", type: "select", options: zoneOpts(), required: true },
    { name: "operator_id", label: "Zone operator", type: "select", options: partyOpts("operator"), required: true },
    { name: "importer_id", label: "Importer of record", type: "select", options: partyOpts("importer") },
    { name: "carrier_id", label: "Carrier", type: "select", options: partyOpts("carrier"), required: true },
    { name: "transport_mode", label: "Mode", type: "select", options: MODE_OPTS() },
    { name: "transport_doc", label: "Bill of lading / AWB", required: true },
    { name: "vessel_voyage", label: "Vessel / voyage / flight" },
    { name: "port_of_entry", label: "Port of entry (code)", required: true },
    { name: "entry_date", label: "Admission date", type: "date", required: true },
    { name: "inbond_ref", label: "Arrived in-bond (7512 no.)" },
    { name: "census_stat", label: "Report Census statistics (214A / 214C)", type: "checkbox" },
    { name: "remarks", label: "Remarks", type: "textarea", wide: true },
  ];
  const vals = existing ? { ...existing, census_stat: !!existing.census_stat } : { entry_date: today() };
  const editor = linesEditor(LINE_COLS(), existing && existing.lines);
  const errBox = h("div");
  const form = h("form", {}, h("div", { class: "fields" }, f.map((x) => field(x, vals[x.name]))),
    h("h2", {}, "Merchandise lines"), editor, errBox,
    h("div", { class: "bar" }, h("button", { class: "primary", type: "submit" }, "Save draft")));
  const close = modal(existing ? `Edit ${existing.doc_no}` : "New e214 application", form);
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const r = await api(existing ? "PUT" : "POST", "/api/admissions" + (existing ? "/" + existing.id : ""), { ...readFields(form, f), lines: editor.read() });
      close(); toast("Saved " + r.doc_no); await refresh(); admissionDetail(r.id);
    } catch (e) { if (e.errors) showErrors(errBox, e); else throw e; }
  });
}

async function admissionDetail(id) {
  const a = await get("/api/admissions/" + id);
  const canEdit = ["draft", "rejected"].includes(a.status);
  const btn = (label, fn, cls) => h("button", { class: cls || "", onclick: fn }, label);
  const doAct = async (action, body, msg) => { if (await act(() => post(`/api/admissions/${id}/${action}`, body), msg)) { close(); await refresh(); if (action !== "delete") admissionDetail(id); } };
  const body = h("div", {},
    h("div", { class: "bar" }, tag(a.status),
      canEdit && btn("Edit", () => { close(); admissionForm(a); }),
      a.status === "draft" && btn("Submit to CBP", () => doAct("submit", {}, "Submitted"), "primary"),
      a.status === "submitted" && btn("Record CBP approval", () => formModal("Record approval", [{ name: "cbp_ref", label: "CBP / ACE reference", required: true }], {}, "Approve & admit to inventory", (v) => doAct("approve", v, "Approved — lots created")), "primary"),
      a.status === "submitted" && btn("Record rejection", () => formModal("Record rejection", [{ name: "reason", label: "Reason", type: "textarea", wide: true, required: true }], {}, "Reject", (v) => doAct("reject", v, "Rejected"))),
      canEdit && btn("Delete", () => confirm("Delete this e214?") && doAct("delete", {}, "Deleted"), "danger"),
      h("span", { class: "grow" }), h("a", { class: "btn", href: `/print/admission/${id}`, target: "_blank" }, "Print worksheet")),
    kv([["Zone", `${a.zone_no || ""} ${a.zone_name || ""}`], ["Operator", a.operator_name], ["Importer", a.importer_name], ["Carrier", a.carrier_name],
      ["Mode / B/L", `${a.transport_mode || ""} ${a.transport_doc || ""}`], ["Port of entry", a.port_of_entry], ["Admission date", a.entry_date],
      ["In-bond ref.", a.inbond_ref], ["CBP ref.", a.cbp_ref], ["Total value", money(a.totals.value)]]),
    a.remarks && h("p", {}, a.remarks),
    a.sheets.map((s) => h("div", { class: "card" }, h("b", {}, `Form ${s.form} — sheet ${s.sheet} of ${s.of}`),
      table([{ h: "#", k: "line_no" }, { h: "Description", k: "description" }, { h: "HTSUS", k: "htsus" }, { h: "COO", k: "coo" },
        { h: "Qty", f: (r) => qty(r.qty), num: true }, { h: "UOM", k: "uom" }, { h: "Value", f: (r) => money(r.value), num: true },
        { h: "Status", f: (r) => tag(r.zone_status, r.zone_status === "ZR" ? "warn" : "") }, { h: "Duty %", k: "duty_rate" }], s.lines))));
  const close = modal(`e214 ${a.doc_no}`, body);
}

/* ---------- e216 ---------- */
VIEWS.permits = async (tab) => {
  tab = tab || VIEWS.permits.tab || "permits"; VIEWS.permits.tab = tab;
  const [permits, acts] = await Promise.all([get("/api/permits"), get("/api/activities")]);
  const tabs = h("div", { class: "tabs" }, [["permits", "Permits (e216)"], ["acts", "Activity log"]].map(([k, t]) =>
    h("a", { class: tab === k ? "on" : "", onclick: () => { VIEWS.permits.tab = k; refresh(); } }, t)));
  const content = tab === "permits"
    ? h("div", { class: "card" }, table([
      { h: "Permit", k: "permit_no" }, { h: "Status", f: (r) => tag(r.status) }, { h: "Type", f: (r) => L.permit_kinds[r.kind] },
      { h: "Zone", k: "zone_no" }, { h: "Activities", f: (r) => r.activity_list.map((a) => L.activities[a]).join(", ") },
      { h: "Valid", f: (r) => `${r.valid_from} → ${r.valid_to}` }, { h: "CBP ref.", k: "cbp_ref" },
    ], permits, permitDetail, "No permits yet."))
    : h("div", { class: "card" }, table([
      { h: "Activity", k: "act_no" }, { h: "Type", f: (r) => L.activities[r.kind] }, { h: "Lot", k: "lot_no" }, { h: "Permit", k: "permit_no" },
      { h: "Date", k: "performed_on" }, { h: "Qty", f: (r) => qty(r.qty), num: true },
      { h: "Result", f: (r) => r.kind === "manufacture" ? `${qty(r.output_qty)} × ${r.output_desc}` : r.note },
      { h: "Status", f: (r) => r.overdue ? tag("overdue", "bad") : tag(r.status) },
      { h: "Return due", k: "expected_return" },
      { h: "", f: (r) => r.status === "open" ? h("button", { class: "sm", onclick: () => returnForm(r) }, "Record return") : "" },
    ], acts, null, "No activity recorded."));
  return h("div", {},
    head("e216 — FTZ Activity Permits", "A permit must be active, cover the date and the activity before any manipulation, manufacture, exhibition, destruction or temporary removal is recorded.",
      h("button", { onclick: permitForm }, "+ New e216 permit"), h("button", { class: "primary", onclick: activityForm }, "+ Record activity")),
    tabs, content);
};

function permitForm() {
  const f = [
    { name: "zone_id", label: "Zone", type: "select", options: zoneOpts(), required: true },
    { name: "operator_id", label: "Operator", type: "select", options: partyOpts("operator"), required: true },
    { name: "kind", label: "Permit type", type: "select", options: Object.entries(L.permit_kinds) },
    { name: "valid_from", label: "Valid from", type: "date", required: true }, { name: "valid_to", label: "Valid to", type: "date", required: true },
    { name: "activities", label: "Activities requested", type: "checks", options: Object.entries(L.activities), wide: true },
    { name: "description", label: "Description of merchandise / activity / location", type: "textarea", wide: true },
  ];
  formModal("New e216 permit", f, { valid_from: today() }, "Save draft", async (v) => { await post("/api/permits", v); toast("Permit saved"); refresh(); });
}
function permitDetail(p) {
  const close = modal(`e216 ${p.permit_no}`, h("div", {},
    h("div", { class: "bar" }, tag(p.status),
      p.status === "draft" && h("button", { class: "primary", onclick: () => formModal("Activate permit", [{ name: "cbp_ref", label: "CBP approval reference", required: true }], {}, "Activate", async (v) => { await post(`/api/permits/${p.id}/activate`, v); close(); refresh(); }) }, "Record CBP approval"),
      p.status === "active" && h("button", { class: "danger", onclick: async () => { if (confirm("Revoke this permit?")) { await act(() => post(`/api/permits/${p.id}/revoke`)); close(); refresh(); } } }, "Revoke"),
      h("span", { class: "grow" }), h("a", { class: "btn", href: `/print/permit/${p.id}`, target: "_blank" }, "Print worksheet")),
    kv([["Zone", p.zone_no], ["Operator", p.operator_name], ["Type", L.permit_kinds[p.kind]], ["Valid", `${p.valid_from} → ${p.valid_to}`],
      ["Activities", p.activity_list.map((a) => L.activities[a]).join(", ")], ["CBP ref.", p.cbp_ref], ["Description", p.description]])));
}

async function activityForm() {
  const [permits, lots] = await Promise.all([get("/api/permits"), get("/api/lots?stock=1")]);
  const avail = lots.filter((l) => l.qty_on_hand > 0);
  const f = [
    { name: "permit_id", label: "e216 permit", type: "select", options: permits.filter((p) => p.status === "active").map((p) => [p.id, `${p.permit_no} (${p.activity_list.join("/")})`]), required: true },
    { name: "kind", label: "Activity", type: "select", options: Object.entries(L.activities), required: true },
    { name: "lot_id", label: "Lot", type: "select", options: avail.map((l) => [l.id, `${l.lot_no} · ${l.description} · ${qty(l.qty_on_hand)} ${l.uom} · ${l.zone_status}`]), required: true },
    { name: "qty", label: "Quantity (input)", type: "number", required: true }, { name: "performed_on", label: "Date", type: "date", required: true },
    { name: "location", label: "New location / exhibit site" }, { name: "expected_return", label: "Expected return (temporary removal)", type: "date" },
    { name: "output_qty", label: "Manufacture: finished qty", type: "number" }, { name: "output_uom", label: "Manufacture: finished UOM" },
    { name: "output_desc", label: "Manufacture: finished description / relabel text" }, { name: "output_htsus", label: "Manufacture: finished HTSUS" },
    { name: "note", label: "Notes (destroy: method & disposition)", type: "textarea", wide: true },
  ];
  formModal("Record zone activity", f, { performed_on: today() }, "Record", async (v) => { await post("/api/activities", v); toast("Activity recorded"); VIEWS.permits.tab = "acts"; refresh(); });
}
function returnForm(a) {
  formModal(`Return ${a.act_no}`, [{ name: "qty", label: `Quantity returned (outstanding ${qty(a.qty - a.returned_qty)})`, type: "number", required: true },
    { name: "date", label: "Return date", type: "date", required: true }, { name: "note", label: "Notes" }], { date: today(), qty: a.qty - a.returned_qty }, "Record return",
    async (v) => { await post(`/api/activities/${a.id}/return`, v); toast("Return recorded"); refresh(); });
}

/* ---------- inventory ---------- */
VIEWS.inventory = async () => {
  const st = VIEWS.inventory.status || "";
  const lots = await get("/api/lots" + (st ? "?status=" + st : ""));
  return h("div", {},
    head("Zone Inventory", "Every lot traces to its e214 line. Quantities change only through e216 activity, withdrawals, in-bond issue or audited adjustments."),
    h("div", { class: "bar" }, h("label", {}, "Status filter ", h("select", { onchange: (e) => { VIEWS.inventory.status = e.target.value; refresh(); } },
      [["", "All"], ...STATUS_OPTS()].map(([v, t]) => h("option", { value: v, selected: v === st }, t)))),
      h("span", { class: "grow" }), h("a", { class: "btn", href: "/export/lots.csv" }, "Export CSV")),
    h("div", { class: "card" }, table([
      { h: "Lot", k: "lot_no" }, { h: "Description", k: "description" }, { h: "HTSUS", k: "htsus" }, { h: "COO", k: "coo" },
      { h: "Status", f: (r) => tag(r.zone_status, r.zone_status === "ZR" ? "warn" : "") },
      { h: "On hand", f: (r) => `${qty(r.qty_on_hand)} ${r.uom}`, num: true }, { h: "Out", f: (r) => qty(r.qty_out), num: true },
      { h: "Value", f: (r) => money(r.value_on_hand), num: true }, { h: "Location", k: "location" },
      { h: "", f: (r) => h("span", {}, h("button", { class: "sm", onclick: () => withdrawForm(r) }, "Withdraw "), " ", h("button", { class: "sm", onclick: () => movements(r) }, "History")) },
    ], lots, null, "No inventory. Approve an e214 to admit merchandise.")));
};
function withdrawForm(lot) {
  const f = [
    { name: "kind", label: "Withdraw to", type: "select", options: [["consumption", "U.S. consumption (7501 entry)"], ["export", "Export"], ["transfer", "Duty-free transfer to another zone"]] },
    { name: "dest_zone_id", label: "Transfer: destination zone", type: "select", options: opts(L.zones.filter((z) => z.id !== lot.zone_id).map((z) => [z.id, z.zone_no])) },
    { name: "qty", label: `Quantity (max ${qty(lot.qty_on_hand)} ${lot.uom})`, type: "number", required: true }, { name: "date", label: "Date", type: "date", required: true },
    { name: "entry_no", label: "Consumption entry no." }, { name: "duty_rate", label: lot.zone_status === "NPF" ? "Duty rate % in force (NPF)" : "Duty rate % (PF: locked)" },
    { name: "export_ref", label: "Export / transfer ref. (AES ITN, e214 no.)" },
  ];
  formModal(`Withdraw ${lot.lot_no} (${lot.zone_status})`, f, { date: today() }, "Withdraw", async (v) => {
    const r = await post("/api/withdrawals", v); toast(r.estimated_duty != null ? `Withdrawn — est. duty ${money(r.estimated_duty)}` : "Withdrawn"); refresh();
  });
  if (lot.zone_status === "ZR") toast("ZR lots may only be exported, destroyed or moved TE/IE");
}
async function movements(lot) {
  const m = await get(`/api/lots/${lot.id}/movements`);
  modal(`History — ${lot.lot_no}`, h("div", {},
    h("div", { class: "bar" }, h("button", { onclick: () => formModal("Adjust on-hand", [{ name: "delta", label: "Quantity change (+/−)", type: "number", required: true }, { name: "reason", label: "Reason", type: "textarea", wide: true, required: true }], {}, "Post adjustment", async (v) => { await post("/api/adjustments", { ...v, lot_id: lot.id }); toast("Adjusted"); refresh(); }) }, "Adjust quantity")),
    table([{ h: "When", k: "ts" }, { h: "Type", k: "kind" }, { h: "Qty", f: (r) => qty(r.qty), num: true }, { h: "Value", f: (r) => money(r.value), num: true },
      { h: "Ref", f: (r) => `${r.ref_type || ""} ${r.ref_no || ""}` }, { h: "Note", k: "note" }, { h: "User", k: "user" }], m)));
}

/* ---------- in-bond ---------- */
VIEWS.inbonds = async () => {
  const type = VIEWS.inbonds.type || "";
  const list = await get("/api/inbonds" + (type ? "?type=" + type : ""));
  const tabs = h("div", { class: "tabs" }, [["", "All"], ...Object.entries(L.inbond_types).map(([k, v]) => [k, `${k} · ${v.code} · ${v.name}`])].map(([k, t]) =>
    h("a", { class: type === k ? "on" : "", onclick: () => { VIEWS.inbonds.type = k; refresh(); } }, t)));
  return h("div", {},
    head("In-Bond Movements (CBP 7512)", "IT (61) moves to another port for consumption entry · TE (62) moves to another port for export · IE (63) is exported at the port of arrival.",
      h("button", { class: "primary", onclick: () => inbondForm(type) }, "+ Issue in-bond")),
    tabs, h("div", { class: "card" }, table([
      { h: "Document", k: "doc_no" }, { h: "Type", f: (r) => `${r.type} (${r.code})` },
      { h: "Status", f: (r) => r.overdue ? tag("overdue", "bad") : tag(r.status) },
      { h: "Route", f: (r) => `${r.origin_port} → ${r.dest_port}` }, { h: "Carrier", k: "carrier_name" }, { h: "B/L", k: "bl_no" },
      { h: "Issued", k: "issued_date" }, { h: "Due", k: "due_date" }, { h: "Value", f: (r) => money(r.total_value), num: true },
    ], list, (r) => inbondDetail(r.id), "No in-bond movements.")));
};

async function inbondForm(type) {
  const lots = (await get("/api/lots?stock=1")).filter((l) => l.qty_on_hand > 0);
  const f = [
    { name: "type", label: "In-bond type", type: "select", options: Object.entries(L.inbond_types).map(([k, v]) => [k, `${k} — ${v.name} (${v.code})`]), required: true },
    { name: "zone_id", label: "Origin zone / warehouse", type: "select", options: zoneOpts(), required: true },
    { name: "carrier_id", label: "Bonded carrier", type: "select", options: partyOpts("carrier"), required: true },
    { name: "consignee_id", label: "Consignee", type: "select", options: partyOpts("consignee") },
    { name: "surety_id", label: "Surety", type: "select", options: partyOpts("surety") }, { name: "bond_ref", label: "Bond no." },
    { name: "mode", label: "Mode", type: "select", options: MODE_OPTS(), required: true }, { name: "bl_no", label: "B/L or AWB", required: true },
    { name: "origin_port", label: "Origin port (code)", required: true }, { name: "dest_port", label: "Destination / export port (code)", required: true },
    { name: "issued_date", label: "Issue date", type: "date", required: true }, { name: "cbp_inbond_no", label: "CBP-assigned in-bond no." },
    { name: "remarks", label: "Remarks", type: "textarea", wide: true },
  ];
  const zone = L.zones[0];
  const editor = linesEditor([
    { k: "lot_id", h: "Zone lot (or manual →)", type: "select", options: [["", "— manual line —"], ...lots.map((l) => [l.id, `${l.lot_no} · ${l.description} · ${qty(l.qty_on_hand)} ${l.uom}`])] },
    { k: "qty", h: "Qty", type: "number" }, { k: "description", h: "Manual: description" }, { k: "htsus", h: "HTSUS" }, { k: "coo", h: "COO" },
    { k: "uom", h: "UOM" }, { k: "value", h: "Value USD", type: "number" }, { k: "zone_status", h: "Status", type: "select", options: STATUS_OPTS(), def: "D" },
  ]);
  const errBox = h("div");
  const form = h("form", {}, h("div", { class: "fields" }, f.map((x) => field(x, { type: type || "TE", issued_date: today(), origin_port: zone && zone.port_code, zone_id: zone && zone.id }[x.name]))),
    h("h2", {}, "Merchandise"), h("p", { class: "sub" }, "Pick a zone lot to draw down inventory, or enter a manual line for cargo that did not come from the zone."), editor, errBox,
    h("div", { class: "bar" }, h("button", { class: "primary", type: "submit" }, "Issue in-bond")));
  const close = modal("Issue in-bond (7512)", form);
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const lines = editor.read().filter((l) => l.lot_id || l.description || l.qty).map((l) => l.lot_id ? { lot_id: +l.lot_id, qty: l.qty } : l);
      const r = await post("/api/inbonds", { ...readFields(form, f), lines });
      close(); toast(`Issued ${r.doc_no}`); VIEWS.inbonds.type = r.type; await refresh(); inbondDetail(r.id);
    } catch (e) { if (e.errors) showErrors(errBox, e); else throw e; }
  });
}

async function inbondDetail(id) {
  const b = await get("/api/inbonds/" + id);
  const step = (label, action, extra, cls) => h("button", { class: cls || "", onclick: () => {
    const fields = [{ name: "date", label: "Date", type: "date", required: true }, ...extra];
    formModal(label, fields, { date: today() }, label, async (v) => { await post(`/api/inbonds/${id}/${action}`, v); close(); await refresh(); inbondDetail(id); });
  } }, label);
  const closeFields = b.type === "IT" ? [{ name: "entry_no", label: "Consumption entry no.", required: true }]
    : [{ name: "export_carrier", label: "Exporting carrier", required: true }, { name: "foreign_dest", label: "Foreign destination", required: true }];
  const close = modal(`${b.type} in-bond ${b.doc_no}`, h("div", {},
    h("div", { class: "bar" }, b.overdue ? tag("overdue", "bad") : tag(b.status),
      b.status === "issued" && step("Depart", "depart", []), ["issued", "in_transit"].includes(b.status) && step("Arrive", "arrive", [], "primary"),
      b.status === "arrived" && step("Close", "close", closeFields, "primary"),
      b.status === "issued" && h("button", { class: "danger", onclick: async () => { if (confirm("Cancel and return stock to inventory?") && await act(() => post(`/api/inbonds/${id}/cancel`))) { close(); refresh(); } } }, "Cancel"),
      h("span", { class: "grow" }), h("a", { class: "btn", href: `/print/inbond/${id}`, target: "_blank" }, "Print worksheet")),
    b.late_arrival && h("div", { class: "alert bad" }, "Arrived after the due date — expect CBP liquidated-damages exposure."),
    kv([["Type", `${b.type} — ${b.type_name} (entry type ${b.code})`], ["Zone", b.zone_no], ["Carrier", b.carrier_name], ["Consignee", b.consignee_name],
      ["Surety / bond", `${b.surety_name || ""} ${b.bond_ref || ""}`], ["Mode", b.mode], ["Route", `${b.origin_port} → ${b.dest_port}`], ["B/L or AWB", b.bl_no],
      ["CBP in-bond no.", b.cbp_inbond_no], ["Issued", b.issued_date], ["Due at destination", b.due_date], ["Arrived", b.arrival_date],
      ["Entry no.", b.entry_no], ["Export date", b.export_date], ["Exporting carrier", b.export_carrier], ["Foreign destination", b.foreign_dest]]),
    h("div", { class: "card" }, table([{ h: "#", k: "line_no" }, { h: "Lot", k: "lot_no" }, { h: "Description", k: "description" }, { h: "HTSUS", k: "htsus" },
      { h: "Qty", f: (r) => `${qty(r.qty)} ${r.uom}`, num: true }, { h: "Value", f: (r) => money(r.value), num: true }, { h: "Status", k: "zone_status" }], b.lines))));
}

/* ---------- setup / audit ---------- */
VIEWS.setup = async () => {
  const s = L.settings;
  return h("div", {},
    head("Setup", "Zones, parties and system settings."),
    h("div", { class: "bar" }, h("button", { onclick: () => formModal("New zone / warehouse", [
      { name: "zone_no", label: "Zone no. (e.g. FTZ 123)", required: true }, { name: "name", label: "Name", required: true }, { name: "grantee", label: "Grantee" },
      { name: "port_code", label: "Port code" }, { name: "address", label: "Address", wide: true }], {}, "Save", async (v) => { await post("/api/zones", v); refresh(); }) }, "+ Zone"),
      h("button", { onclick: () => formModal("New party", [
        { name: "kind", label: "Type", type: "select", options: L.party_kinds.map((k) => [k, k]) }, { name: "name", label: "Name", required: true },
        { name: "ident", label: "EIN / importer no. / SCAC" }, { name: "address", label: "Address", wide: true }], {}, "Save", async (v) => { await post("/api/parties", v); refresh(); }) }, "+ Party")),
    h("h2", {}, "Zones"), h("div", { class: "card" }, table([{ h: "Zone", k: "zone_no" }, { h: "Name", k: "name" }, { h: "Grantee", k: "grantee" }, { h: "Port", k: "port_code" }], L.zones, null, "Add a zone first.")),
    h("h2", {}, "Parties"), h("div", { class: "card" }, table([{ h: "Type", k: "kind" }, { h: "Name", k: "name" }, { h: "ID", k: "ident" }], L.parties, null, "No parties.")),
    h("h2", {}, "Settings"), h("div", { class: "card" }, (() => {
      const f = [{ name: "lines_per_sheet", label: "Lines per 214 / 214A sheet" }, { name: "transit_days_IT", label: "IT transit days" },
        { name: "transit_days_TE", label: "TE transit days" }, { name: "transit_days_IE", label: "IE transit days" }, { name: "expiry_warning_days", label: "Permit expiry warning (days)" }];
      const errBox = h("div");
      const form = h("form", {}, h("p", { class: "sub" }, "Transit-day defaults are placeholders — set them to the time limit CBP grants at your port for each mode."),
        h("div", { class: "fields" }, f.map((x) => field(x, s[x.name]))), errBox, h("div", { class: "bar" }, h("button", { class: "primary" }, "Save settings")));
      form.addEventListener("submit", async (ev) => { ev.preventDefault(); try { await api("PUT", "/api/settings", readFields(form, f)); toast("Settings saved"); } catch (e) { showErrors(errBox, e); } });
      return form;
    })()));
};

VIEWS.reports = async () => {
  const r = await get("/api/reports");
  const sec = (t, cols, d) => [h("h2", {}, t), h("div", { class: "card" }, table(cols, d, null, "No data yet."))];
  return h("div", {}, head("Reports", "Weekly entry basis, inventory reconciliation and activity summaries for CBP / FTZ Board / internal use."),
    sec("Weekly entry filing — consumption withdrawals", [{ h: "Week", k: "week" }, { h: "Status", k: "zone_status" }, { h: "Withdrawals", k: "withdrawals", num: true },
      { h: "Value", f: (x) => money(x.value), num: true }, { h: "Est. duty", f: (x) => money(x.est_duty), num: true }, { h: "Entries", k: "entries" }], r.weekly_entries),
    sec("Inventory reconciliation by zone and status", [{ h: "Zone", k: "zone_no" }, { h: "Status", k: "zone_status" }, { h: "Admitted", k: "admitted", num: true },
      { h: "On hand", k: "on_hand", num: true }, { h: "Temp. out", k: "out_temp", num: true }, { h: "Value", f: (x) => money(x.value), num: true }], r.inventory_reconciliation),
    sec("Activity summary (e216)", [{ h: "Activity", f: (x) => L.activities[x.kind] }, { h: "Count", k: "n", num: true }, { h: "Qty", k: "qty", num: true }], r.activity_summary),
    sec("In-bond summary", [{ h: "Type", k: "type" }, { h: "Status", k: "status" }, { h: "Count", k: "n", num: true }], r.inbond_summary));
};

VIEWS.account = async () => {
  ME = await get("/auth/me");
  const pwFields = [{ name: "current", label: "Current password", type: "password", required: true }, { name: "new", label: "New password (10+ characters)", type: "password", required: true },
    { name: "again", label: "Repeat new password", type: "password", required: true }];
  return h("div", {}, head("My Account", "Your sign-in details."),
    h("div", { class: "card" }, kv([["User name", ME.username], ["Role", ME.role + (ME.is_demo ? " (shared demo account)" : "")], ["Email (for password reset)", ME.email]])),
    ME.is_demo ? h("div", { class: "alert" }, "This is the shared demo account. Its password and email can't be changed.") : h("div", { class: "bar" },
      h("button", { class: "primary", onclick: () => formModal("Change password", pwFields, {}, "Change password", async (v) => {
        if (v.new !== v.again) throw { errors: ["The new passwords do not match"] };
        await post("/auth/password", { current: v.current, new: v.new }); toast("Password changed"); }) }, "Change password"),
      h("button", { onclick: () => formModal("Email for password reset", [{ name: "email", label: "Email address", type: "email" }, { name: "current", label: "Current password", type: "password", required: true }],
        { email: ME.email }, "Save email", async (v) => { await post("/auth/email", v); toast("Email saved"); refresh(); }) }, "Set email")));
};

VIEWS.users = async () => {
  if (!isAdmin()) throw { errors: ["Administrators only"] };
  const users = await get("/api/users");
  const setPw = (u) => formModal(`New password for ${u.username}`, [{ name: "password", label: "New password (10+ characters)", type: "password", required: true }], {}, "Set password",
    async (v) => { await post(`/api/users/${u.id}/password`, v); toast("Password set - they are signed out everywhere"); });
  return h("div", {}, head("Users", "People who can sign in. Passwords are stored hashed; an email lets a person reset their own password.",
    h("button", { class: "primary", onclick: () => formModal("New user", [{ name: "username", label: "User name", required: true }, { name: "email", label: "Email", type: "email" },
      { name: "role", label: "Role", type: "select", options: [["user", "User"], ["admin", "Administrator"]] }, { name: "password", label: "Temporary password (10+ characters)", type: "password", required: true }],
      {}, "Create user", async (v) => { await post("/api/users", v); toast("User created"); refresh(); }) }, "+ New user")),
    h("div", { class: "card" }, table([{ h: "User", k: "username" }, { h: "Email", k: "email" }, { h: "Role", k: "role" },
      { h: "Status", f: (u) => u.is_demo ? tag("demo", "warn") : tag(u.active ? "active" : "disabled", u.active ? "ok" : "bad") }, { h: "Last sign-in", k: "last_login" },
      { h: "", f: (u) => u.is_demo ? "" : h("span", {}, h("button", { class: "sm", onclick: () => setPw(u) }, "Set password"), " ",
        h("button", { class: "sm", onclick: async () => { if (await act(() => post(`/api/users/${u.id}/${u.active ? "disable" : "enable"}`))) refresh(); } }, u.active ? "Disable" : "Enable")) }], users)));
};

VIEWS.audit = async () => {
  const log = await get("/api/audit");
  const ex = ["admissions", "admission_lines", "lots", "movements", "permits", "activities", "inbonds", "inbond_lines", "audit"];
  return h("div", {},
    head("Audit & Export", "Every change is recorded with the operator name. Export tables for CBP audit requests or take a full database backup."),
    h("div", { class: "bar" }, ex.map((t) => h("a", { class: "btn", href: `/export/${t}.csv` }, t + ".csv")), isAdmin() && h("a", { class: "btn primary", href: "/backup" }, "Full backup (.db)")),
    h("div", { class: "card" }, table([{ h: "When", k: "ts" }, { h: "User", k: "user" }, { h: "Action", k: "action" }, { h: "Entity", f: (r) => `${r.entity} #${r.entity_id}` }, { h: "Detail", k: "detail" }], log)));
};

(async () => {
  try { ME = await get("/auth/me"); } catch (e) { return; }
  buildShell();
  const h0 = (location.hash || "#dashboard").slice(1);
  go(h0 in VIEWS ? h0 : "dashboard");
})();
