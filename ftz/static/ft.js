"use strict";
/* CATAIR FT (Input) filing builder. Uses the helpers from app.js (h, get, post, modal, tag, table, toast, ...).
   The form is generated from the layout the server publishes (/api/ft/spec), which is checked against the CBP PDF. */

let FTSPEC = null;
const FT = { current: null, S: null, timer: null, dirty: false, tab: "checks", spaces: true, notes: [] };

const ftMeta = (rid, f) => `${rid} · pos ${f.start}-${f.end} · ${f.len}${f.cls} · ${f.req === "M" ? "required" : f.req === "C" ? "conditional" : "optional"}`;
const ftFields = (rid, skip = []) => FTSPEC.layout[rid].fields.filter((f) => f.key && f.key !== "_id" && !f.derived && !skip.includes(f.key));

function ftChanged() {
  FT.dirty = true;
  clearTimeout(FT.timer);
  FT.timer = setTimeout(ftCheck, 700);
}
async function ftCheck() {
  if (!FT.S) return;
  try { FT.S.check = await post("/api/ft/check", { data: FT.S.data }); ftSide(); } catch (e) { /* the form stays usable if a check fails */ }
}

/* one input bound to obj[key] */
function ftInput(rid, f, obj, key = f.key, extra = {}) {
  let el;
  if (f.choices) {
    el = h("select", {}, f.choices.map((c) => h("option", { value: c }, c === "" ? "—" : c)));
  } else if (f.date) el = h("input", { type: "date" });
  else if (f.cls === "N") el = h("input", { type: "text", inputmode: "decimal", autocomplete: "off" });
  else el = h("input", { type: "text", maxlength: f.cls === "X" || f.cls === "AN" || f.cls === "A" ? f.len : null, autocomplete: "off", class: "up" });
  el.value = obj[key] ?? (f.choices ? f.choices[0] : "");
  if (f.choices && obj[key] == null) obj[key] = el.value;
  el.addEventListener("input", () => { obj[key] = el.value; ftChanged(); extra.onInput && extra.onInput(el.value); });
  el.addEventListener("change", () => extra.onChange && extra.onChange(el.value));
  return h("label", { class: "ftf" + (f.req === "M" ? " req" : ""), title: f.help || "" }, h("span", {}, f.label), el, h("small", {}, ftMeta(rid, f)));
}
const ftGrid = (rid, obj, skip = [], extra = {}) => h("div", { class: "ftgrid" }, ftFields(rid, skip).map((f) => ftInput(rid, f, obj, f.key, extra[f.key])));

/* editable list of plain strings */
function ftList(arr, label, max, rerender, { rows = 1, ph = "" } = {}) {
  const box = h("div", { class: "ftlist" }, h("b", {}, label));
  const draw = () => {
    box.replaceChildren(h("b", {}, `${label} (${arr.length}/${max})`),
      ...arr.map((v, i) => h("div", { class: "ftrow" },
        (rows > 1 ? h("textarea", { rows, class: "up", placeholder: ph }, v) : h("input", { class: "up", value: v, placeholder: ph })),
        h("button", { type: "button", class: "sm danger", onclick: () => { arr.splice(i, 1); ftChanged(); draw(); rerender && rerender(); } }, "✕"))),
      h("button", { type: "button", class: "sm", disabled: arr.length >= max, onclick: () => { arr.push(""); ftChanged(); draw(); } }, "+ Add"));
    box.querySelectorAll("input,textarea").forEach((el, i) => el.addEventListener("input", () => { arr[i] = el.value; ftChanged(); }));
  };
  draw();
  return box;
}

const ftNewLine = (bill) => ({ line_no: Math.max(0, ...bill.lines.map((l) => +l.line_no || 0)) + 1, zone_status: "N", hmf: 0, refs: [{ description: "", qualifier: "MID", ref_id: "" }], remarks: [] });
const ftNewBill = () => ({ bill: "", inbond_numbers: [], bonded_carriers: [], containers: [], lines: [] });

function ftLine(bill, ln, li, action, redraw) {
  const sc = action === "S";
  const skip51 = sc ? [] : ["existing_status", "requested_status", "affected_qty", "inventory_uom"];
  const skip50 = sc ? ["qty1", "uom1", "qty2", "uom2"] : [];
  return h("div", { class: "ftline" },
    h("div", { class: "bar" }, h("b", { class: "grow" }, `HTS line ${ln.line_no || li + 1} · FT50 / FT51` + (ln.spi_secondary === "X" ? " · Article Set header" : ln.spi_secondary === "V" ? " · Article Set component" : "")),
      h("button", { type: "button", class: "sm danger", onclick: () => { bill.lines.splice(li, 1); ftChanged(); redraw(); } }, "Remove line")),
    ftGrid("FT50", ln, skip50), ftGrid("FT51", ln, skip51, { zone_status: {} }),
    sc ? null : h("div", { class: "ftrefs" }, h("b", {}, "FT60 references (a MID is required on every line)"),
      ...ln.refs.map((r, ri) => h("div", { class: "ftgrid ftref" }, ftFields("FT60").map((f) => ftInput("FT60", f, r)),
        h("button", { type: "button", class: "sm danger", onclick: () => { ln.refs.splice(ri, 1); ftChanged(); redraw(); } }, "✕"))),
      h("button", { type: "button", class: "sm", onclick: () => { ln.refs.push({ description: ln.refs[0] ? ln.refs[0].description : "", qualifier: "", ref_id: "" }); ftChanged(); redraw(); } }, "+ FT60")),
    sc ? null : ftList(ln.remarks, "FT61 remarks (optional)", FTSPEC.limits.remarks61, null, { rows: 2 }));
}

function ftBill(cv, b, bi, action, redraw) {
  return h("div", { class: "ftbill" },
    h("div", { class: "bar" }, h("b", { class: "grow" }, `Bill of lading ${bi + 1} · FT40`), h("button", { type: "button", class: "sm danger", onclick: () => { cv.bills.splice(bi, 1); ftChanged(); redraw(); } }, "Remove bill")),
    ftGrid("FT40", b),
    action === "S" ? null : h("div", { class: "ftcols3" }, ftList(b.inbond_numbers, "FT41 in-bond numbers", FTSPEC.limits.inbonds), ftList(b.bonded_carriers, "FT42 bonded carrier IOR", FTSPEC.limits.carriers),
      ftList(b.containers, "FT43 containers", FTSPEC.limits.containers)),
    h("h4", {}, `HTS lines in this bill (${b.lines.length})`),
    ...b.lines.map((ln, li) => ftLine(b, ln, li, action, redraw)),
    h("button", { type: "button", class: "sm", onclick: () => { b.lines.push(ftNewLine(b)); ftChanged(); redraw(); } }, "+ HTS line (FT50/FT51)"));
}

function ftConveyance(d, cv, ci, redraw) {
  const action = d.header.action;
  return h("div", { class: "card ftsec" },
    h("div", { class: "bar" }, h("h3", { class: "grow" }, `Conveyance ${ci + 1} · FT20`), h("button", { type: "button", class: "sm danger", onclick: () => { d.conveyances.splice(ci, 1); ftChanged(); redraw(); } }, "Remove conveyance")),
    h("p", { class: "sub" }, `Admission type ${cv.admission_type || "?"}: ${FTSPEC.admission_types[cv.admission_type] || ""}. For D, O and Z the conveyance details are not reported.`),
    ftGrid("FT20", cv, [], { admission_type: { onChange: redraw } }),
    ...cv.bills.map((b, bi) => ftBill(cv, b, bi, action, redraw)),
    h("button", { type: "button", class: "sm", onclick: () => { cv.bills.push(ftNewBill()); ftChanged(); redraw(); } }, "+ Bill of lading (FT40)"));
}

function ftForm() {
  const d = FT.S.data;
  d.header = d.header || {}; d.replace = d.replace || { reason_codes: [], remarks: [] }; d.conveyances = d.conveyances || []; d.delete_remarks = d.delete_remarks || [];
  const root = h("div", { class: "ftform" });
  const draw = () => {
    const action = (d.header.action || "A").toUpperCase();
    const secs = [h("div", { class: "card ftsec" }, h("h3", {}, "Admission header · FT10"),
      h("p", { class: "sub" }, `Admission number: `, h("b", {}, ((d.header.zone_id || "") + (d.header.calendar_year || "") + (d.header.control_number || "")).toUpperCase() || "—"), ` · Expanded Zone ID indicator is set automatically.`),
      ftGrid("FT10", d.header, [], { action: { onChange: draw } }))];
    if (action === "R") {
      secs.push(h("div", { class: "card ftsec" }, h("h3", {}, "Replace request · FT11 / FT12"),
        h("p", { class: "sub" }, "A Replace retransmits the whole admission with the changes. Choose every reason that applies; reason 08 (Other) requires a remark."),
        h("div", { class: "ftgrid" }, ftInput("FT11", FTSPEC.layout.FT11.fields.find((f) => f.key === "contact_name"), d.replace), ftInput("FT11", FTSPEC.layout.FT11.fields.find((f) => f.key === "contact_phone"), d.replace)),
        h("div", { class: "checks" }, Object.entries(FTSPEC.reasons).map(([c, t]) => h("label", {}, h("input", { type: "checkbox", checked: (d.replace.reason_codes || []).includes(c),
          onchange: (e) => { const s = new Set(d.replace.reason_codes || []); e.target.checked ? s.add(c) : s.delete(c); d.replace.reason_codes = [...s].sort(); ftChanged(); } }), `${c} ${t}`))),
        ftList((d.replace.remarks = d.replace.remarks || []), "FT12 remarks", FTSPEC.limits.remarks12, null, { rows: 2 })));
    }
    if (action === "D") {
      secs.push(h("div", { class: "card ftsec" }, h("h3", {}, "Delete comments · FT61"), h("p", { class: "sub" }, "A Delete sends only FT10 and optional comments, for example the entry or export document number."),
        ftList(d.delete_remarks, "FT61 comments", FTSPEC.limits.remarks61, null, { rows: 2 })));
    } else {
      d.conveyances.forEach((cv, ci) => secs.push(ftConveyance(d, cv, ci, draw)));
      secs.push(h("button", { type: "button", class: "primary", onclick: () => { d.conveyances.push({ admission_type: action === "S" ? "C" : "A", bills: [ftNewBill()] }); ftChanged(); draw(); } }, "+ Conveyance (FT20)"));
    }
    root.replaceChildren(...secs);
  };
  draw();
  return root;
}

/* ---------- right-hand panel: checks / records / usage map ---------- */
function ftRecordLine(r) {
  const out = h("span", { class: "ftrec" });
  r.text.split(/( +)/).forEach((p) => { if (!p) return; out.append(p[0] === " " ? h("span", { class: FT.spaces ? "sp" : "" }, FT.spaces ? "·".repeat(p.length) : p) : p); });
  return h("div", { class: "ftline80" }, h("b", { class: "rid rid" + r.id }, r.id), out);
}
function ftMapTable(key, counts) {
  const m = key === "FZ" ? FTSPEC.fz.map : FTSPEC.maps[key];
  const [title, rows] = [m.title, m.rows];
  return h("div", {}, h("h4", {}, title), h("table", { class: "ftmap" }, h("tr", {}, ["Record", "Description", "Req.", "Max", "Loop", "In this filing"].map((t) => h("th", {}, t))),
    rows.map(([id, desc, req, max, loop, depth]) => {
      const n = id.includes("A, B") || id.includes("Y, Z") ? null : (counts || {})[id] || 0;
      const missing = n === 0 && req === "M";
      return h("tr", { class: missing ? "miss" : "" }, h("td", { style: `padding-left:${8 + depth * 14}px` }, h("b", {}, id)), h("td", {}, desc), h("td", {}, req), h("td", { class: "num" }, max), h("td", { class: "num" }, loop),
        h("td", { class: "num" }, n === null ? "added at export" : n ? tag(String(n), "ok") : missing ? tag("missing", "bad") : "—"));
    })));
}
function ftSide() {
  const side = $("#ftside");
  if (!side) return;
  const c = FT.S.check || { errors: [], warnings: [], records: [], counts: {} };
  const tabs = h("div", { class: "tabs" }, [["checks", `Checks (${c.errors.length} errors, ${c.warnings.length} warnings)`], ["records", `Records (${c.records.length})`], ["map", "Usage map"]].map(([k, t]) =>
    h("a", { class: FT.tab === k ? "on" : "", onclick: () => { FT.tab = k; ftSide(); } }, t)));
  let body;
  if (FT.tab === "checks") {
    body = h("div", {}, c.ok ? h("div", { class: "alert ok" }, "No blocking problems: the records can be exported.") : h("div", { class: "errs" }, h("b", {}, `${c.errors.length} problem(s) to fix before export`), h("ul", {}, c.errors.map((m) => h("li", {}, m)))),
      c.warnings.length ? h("div", { class: "alert" }, h("b", {}, "Review"), h("ul", {}, c.warnings.map((m) => h("li", {}, m)))) : null,
      FT.notes.length ? h("div", { class: "alert" }, h("b", {}, "Needs your input (from the e214 prefill)"), h("ul", {}, FT.notes.map((m) => h("li", {}, m)))) : null,
      FT.S.kind === "FZ" ? h("details", {}, h("summary", {}, "Checks CBP performs when it receives the file (not possible here)"), h("ul", { class: "sub" }, [
        "The admission, bill of lading, in-bond and container numbers are on file and active in CBP's manifest system",
        "An admission named in the message is in AUTHORIZED status (CONCURRED for a Post Admission Correction request)",
        "A bonded carrier holds an active Activity Type 2 bond; the FIRMS code is an active type 4 FTZ site in the same port or port cluster",
        "A PTT can only be cancelled with the PID CBP returned, and a container must already be tied to the bill of lading"].map((m) => h("li", {}, m))))
      : h("details", {}, h("summary", {}, "Checks CBP performs when it receives the file (not possible here)"), h("ul", { class: "sub" }, [
        "The bill of lading is on file in CBP's manifest system for the named conveyance (Regular admissions)",
        "FDA Prior Notice has been satisfied for every bill of lading",
        "The Zone Operator holds an active type 4 FTZ bond; a bonded carrier holds an active Activity Type 2 bond",
        "The FIRMS code is an active FTZ site in the right port, and approved for Direct Delivery when marked Y",
        "Each HTS number is valid in the current tariff, and in-bond numbers are on file",
        "The admission number is not already on file (Add) or is in Accepted/Authorized status (Replace, Delete)"].map((m) => h("li", {}, m)))));
  } else if (FT.tab === "records") {
    body = h("div", {}, h("div", { class: "bar" }, h("label", { style: "display:flex;gap:6px;align-items:center" }, h("input", { type: "checkbox", checked: FT.spaces, style: "width:auto", onchange: (e) => { FT.spaces = e.target.checked; ftSide(); } }), "Show spaces as ·"),
      h("span", { class: "grow" }), h("button", { class: "sm", onclick: async () => { try { await navigator.clipboard.writeText(c.records.map((r) => r.text).join("\n")); toast("Records copied"); } catch { toast("Select the text and copy it"); } } }, "Copy records")),
      h("div", { class: "ftscroll" }, h("div", { class: "ftline80 ruler" }, h("b", { class: "rid" }, ""), h("span", { class: "ftrec" }, FTSPEC.ruler)), ...c.records.map(ftRecordLine)),
      h("p", { class: "sub" }, "Each record is exactly 80 characters. The ruler marks position 1, 11, 21 … 71."));
  } else {
    body = h("div", {}, h("p", { class: "sub" }, "The structure CBP defines for this kind of transaction. Rows in red are mandatory and missing."), ftMapTable(c.usage_map || "main", c.counts),
      c.usage_map && c.usage_map !== "main" && c.usage_map !== "FZ" ? h("details", {}, h("summary", {}, "Show the standard map"), ftMapTable("main", c.counts)) : null);
  }
  side.replaceChildren(tabs, body);
}

/* ---------- editor shell, save, export ---------- */
async function ftSave() {
  const S = FT.S;
  const r = await act(() => post("/api/ft", { id: S.id, kind: S.kind || "FT", label: $("#ftlabel").value, admission_id: S.admission_id, data: S.data }), "Filing saved");
  if (r) { FT.S = { ...r }; FT.dirty = false; ftSide(); $("#ftstatus").replaceChildren(tag(r.status)); }
}
function ftExport() {
  const abi = FTSPEC.abi, missing = Object.entries({ "Site code": abi.site_code, "Sender ID": abi.sender_id, "Filer code": abi.filer_code, "Port code": abi.port_code }).filter(([, v]) => !v).map(([k]) => k);
  const mode = h("select", {}, h("option", { value: "batch" }, `Complete batch: A, B, ${FT.S.kind || "FT"} records, Y, Z`), h("option", { value: "body" }, FT.S.kind === "FZ" ? "Transaction records only (FZ10 … FZ20)" : "Transaction records only (FT10 … FT61)"));
  const eol = h("select", {}, h("option", { value: "lf" }, "LF (Unix)"), h("option", { value: "crlf" }, "CRLF (Windows)"));
  const pw = h("input", { type: "password", maxlength: 6, autocomplete: "off", class: "up", placeholder: "6 characters" });
  const out = h("div");
  const pwBox = h("label", {}, "ABI communication password (used for this download only, never saved)", pw);
  mode.addEventListener("change", () => (pwBox.hidden = mode.value !== "batch"));
  const close = modal(`Export CATAIR ${FT.S.kind || "FT"} file`, h("div", {},
    h("p", { class: "sub" }, "This prepares the file for your ABI connection or broker software. ", h("b", {}, "This system does not transmit anything to CBP.")),
    missing.length > 0 && h("div", { class: "alert" }, `Complete batch needs these under Setup → Settings → ABI envelope: ${missing.join(", ")}.`),
    h("div", { class: "fields" }, h("label", {}, "File contents", mode), h("label", {}, "Line ending (ask your ABI software if unsure)", eol), pwBox), out,
    h("div", { class: "bar" }, h("button", { class: "primary", onclick: async () => {
      try {
        const r = await post(`/api/ft/${FT.S.id}/export`, { mode: mode.value, eol: eol.value, password: pw.value });
        const a = h("a", { href: URL.createObjectURL(new Blob([r.text], { type: "text/plain" })), download: r.filename });
        document.body.append(a); a.click(); a.remove();
        FT.dirty = false; pw.value = "";
        out.replaceChildren(h("div", { class: "alert ok" }, `Downloaded ${r.filename}: ${r.line_count} records. Fingerprint ${r.sha256.slice(0, 16)}…`));
        $("#ftstatus").replaceChildren(tag("exported"));
      } catch (e) { if (e.errors) showErrors(out, e); else throw e; }
    } }, "Download file"), h("button", { onclick: () => close() }, "Cancel"))), { narrow: true });
}
async function ftHistory() {
  const rows = await get(`/api/ft/${FT.S.id}/exports`);
  modal("Export history", table([{ h: "When", k: "ts" }, { h: "By", k: "user" }, { h: "Contents", k: "mode" }, { h: "Records", k: "line_count", num: true }, { h: "Line ending", k: "eol" }, { h: "SHA-256 of the records", f: (r) => h("code", {}, r.sha256.slice(0, 20) + "…") }], rows, null, "Not exported yet."));
}

function ftEditor() {
  const S = FT.S;
  const back = () => { if (FT.dirty && !confirm("Leave without saving your changes?")) return; FT.current = null; FT.S = null; FT.notes = []; refresh(); };
  const root = h("div", { class: "ft" },
    h("div", { class: "bar" }, h("button", { onclick: back }, "← All filings"), h("input", { id: "ftlabel", value: S.label || "", placeholder: "Label", style: "max-width:300px" }),
      h("span", { id: "ftstatus" }, tag(S.status || "draft")), h("span", { class: "grow" }),
      S.id && S.id && h("button", { onclick: ftResponses }, "CBP responses"), S.id && h("button", { onclick: () => ftResponse(S.id) }, "Paste CBP response…"),
      S.id && h("button", { onclick: ftHistory }, "Export history"),
      h("button", { onclick: ftSave }, "Save"), h("button", { class: "primary", onclick: async () => { await ftSave(); if (FT.S.check && FT.S.check.ok) ftExport(); else { FT.tab = "checks"; ftSide(); toast("Fix the problems listed first"); } } }, "Export…")),
    h("div", { class: "ftcols" }, S.kind === "FZ" ? fzForm() : ftForm(), h("div", { class: "card ftsidebox", id: "ftside" })));
  setTimeout(ftSide, 0);
  return root;
}

async function ftOpen(id, notes) {
  FT.S = await get(`/api/ft/${id}`);
  FT.current = id; FT.dirty = false; FT.tab = notes && notes.length ? "checks" : FT.tab; FT.notes = notes || [];
  go("catair");
}
async function ftFromAdmission() {
  const adms = await get("/api/admissions");
  if (!adms.length) return toast("Create an e214 admission first, or start a blank filing");
  formModal("Build from an e214 admission", [{ name: "id", label: "Admission", type: "select", wide: true, options: adms.map((a) => [a.id, `${a.doc_no} · ${a.status} · ${a.transport_doc || "no B/L"} · ${a.line_count} line(s)`]), required: true }], {}, "Build filing",
    async (v) => { const r = await post(`/api/ft/from-admission/${v.id}`); toast("Filing created: fill in the items listed"); await ftOpen(r.id, r.notes); });
}
async function ftBlank() {
  const r = await post("/api/ft", { label: "New FT filing", data: { header: { action: "A", direct_delivery: "N", abi_filer: FTSPEC.abi.filer_code, calendar_year: String(new Date().getFullYear()).slice(2) },
    replace: { reason_codes: [], remarks: [] }, conveyances: [{ admission_type: "A", bills: [{ ...ftNewBill(), lines: [{ line_no: 1, zone_status: "N", hmf: 0, refs: [{ description: "", qualifier: "MID", ref_id: "" }], remarks: [] }] }] }], delete_remarks: [] } });
  await ftOpen(r.id, []);
}
function ftUsageMaps() {
  modal("CATAIR usage maps", h("div", {}, h("p", { class: "sub" }, `From ${FTSPEC.source.ft.title}, version ${FTSPEC.source.ft.version} (${FTSPEC.source.ft.date}), Pub # ${FTSPEC.source.ft.pub}. Loops nest: a conveyance (up to 999) holds bills (up to 9,999), and each bill holds its HTS lines (up to 9,999).`),
    ["main", "T", "S", "D", "FZ"].map((k) => ftMapTable(k, null))));
}

VIEWS.catair = async () => {
  if (!FTSPEC) FTSPEC = await get("/api/ft/spec");
  if (FT.current != null && FT.S) return ftEditor();
  const list = await get("/api/ft");
  return h("div", {},
    head("CATAIR FT Filing", `Builds the e214 FT (Input) records exactly as the ACE CATAIR Foreign Trade Zone chapter (v${FTSPEC.source.ft.version}, ${FTSPEC.source.ft.date}) defines them: 80-character fixed-width records in the usage-map structure. It prepares the file; it does not transmit it to CBP.`,
      h("button", { onclick: ftUsageMaps }, "Usage maps"), h("button", { onclick: () => ftResponse(null) }, "Paste CBP response…"), h("button", { onclick: ftFromAdmission }, "Build from an e214…"),
      h("button", { onclick: fzBlank }, "+ FZ action (concur · PTT · arrival)"), h("button", { class: "primary", onclick: ftBlank }, "+ Blank FT filing")),
    h("div", { class: "card" }, table([{ h: "Type", f: (r) => tag(r.kind || "FT", "") }, { h: "Admission / bill no.", k: "admission_number" }, { h: "Label", k: "label" },
      { h: "Action", f: (r) => r.kind === "FZ" ? (r.action || "").split(",").map((c) => (FTSPEC.fz.actions[c] || {}).title || c).join("; ") : ({ A: "Add", D: "Delete", R: "Replace", S: "Status change" })[r.action] || r.action || "" },
      { h: "Status", f: (r) => tag(r.status) }, { h: "Updated", k: "updated_at" }, { h: "By", k: "updated_by" }], list, (r) => ftOpen(r.id), "No filings yet. Build one from an e214 admission, or start a blank filing.")));
};

/* ---------- FZ (Input): concurrence, PTT, operator acceptance / refusal, arrival, post admission correction ---------- */
const fzF = (rid, key) => FTSPEC.fz.layout[rid].fields.find((f) => f.key === key);
const fzIn = (rid, key, obj, objKey = key, extra = {}) => ftInput(rid, fzF(rid, key), obj, objKey, extra);
const QUAL = { "1": "1 · Admission number", "2": "2 · Bill of lading", "3": "3 · In-bond number", "4": "4 · Container" };

function fzSelect(label, value, options, on, hint = "") {
  const el = h("select", {}, options.map(([v, t]) => h("option", { value: v }, t)));
  el.value = value ?? "";
  el.addEventListener("change", () => on(el.value));
  return h("label", { class: "ftf req", title: hint }, h("span", {}, label), el);
}
function fzCheck(label, obj, key, on) {
  const el = h("input", { type: "checkbox", style: "width:auto", checked: !!obj[key], onchange: () => { obj[key] = el.checked; ftChanged(); on && on(); } });
  return h("label", { class: "ftf", style: "display:flex;gap:6px;align-items:center" }, el, h("span", {}, label));
}

function fzAction(d, a, i, draw) {
  const info = FTSPEC.fz.actions[a.action] || { qualifiers: ["1", "2", "3", "4"] };
  const code = a.action, q = a.qualifier;
  const conc = ["A", "B", "C", "D"].includes(code);
  if (conc && a.delivery_code == null) a.delivery_code = "FI";
  a.containers = a.containers || []; a.remarks = a.remarks || [];
  const parts = [h("div", { class: "ftgrid" },
    fzSelect("Action", code, Object.entries(FTSPEC.fz.actions).map(([k, v]) => [k, `${k} — ${v.title}`]), (v) => { a.action = v; const ok = FTSPEC.fz.actions[v].qualifiers; if (!ok.includes(a.qualifier)) a.qualifier = ok[0]; ftChanged(); draw(); }, "FZ10 pos 39-40"),
    fzSelect("Action Qualifier", q, info.qualifiers.map((k) => [k, QUAL[k]]), (v) => { a.qualifier = v; ftChanged(); draw(); }, "FZ10 pos 3"),
    fzIn("FZ10", "id_number", a))];
  const g = [];
  if (conc || code === "F" || code === "I") g.push(fzIn("FZ10", "received_qty", a));
  if (conc) g.push(fzIn("FZ10", "delivery_code", a));
  if (code === "F") g.push(fzIn("FZ10", "irs", a));
  if (code === "F" || code === "I") g.push(fzIn("FZ10", "firms", a));
  if (["2", "3", "4"].includes(q) && !["G", "H", "J"].includes(code)) g.push(fzIn("FZ11", "admission_number", a));
  if (g.length) parts.push(h("div", { class: "ftgrid" }, g));
  if (!["G", "H", "J"].includes(code)) {
    parts.push(h("div", { class: "checks" }, fzCheck("Direct Delivery (no admission number needed)", a, "direct_delivery"), fzCheck("Bill of lading arrived by air", a, "air", draw)));
    if (a.air) parts.push(h("div", { class: "ftgrid" }, fzIn("FZ10", "airport", a), fzIn("FZ11", "carrier_code", a), fzIn("FZ11", "flight", a), fzIn("FZ11", "sched_arrival", a)));
  }
  if (q === "4" || a.containers.length) {
    const box = h("div", { class: "ftlist" }, h("b", {}, `Containers (FZ11) · ${a.containers.length}`));
    a.containers.forEach((c, ci) => box.append(h("div", { class: "ftrow" }, h("div", { class: "ftgrid grow" }, fzIn("FZ11", "container", c), fzIn("FZ11", "container_qty", c, "qty")),
      h("button", { type: "button", class: "sm danger", onclick: () => { a.containers.splice(ci, 1); ftChanged(); draw(); } }, "✕"))));
    box.append(h("button", { type: "button", class: "sm", onclick: () => { a.containers.push({ container: "", qty: "" }); ftChanged(); draw(); } }, "+ Container"));
    parts.push(box);
  }
  if (code === "K") parts.push(h("div", { class: "ftgrid" }, h("label", { class: "ftf req", title: "FZ12" }, h("span", {}, "PID returned by CBP (FZ12, PTT Unique Identifier)"),
    (() => { const el = h("input", { class: "up", maxlength: 19, value: a.pid || "" }); el.addEventListener("input", () => { a.pid = el.value; ftChanged(); }); return el; })(), h("small", {}, "FZ12 · max 19"))));
  if (code === "J") {
    const pc = (a.correction = a.correction || { reason: "01", ref: { qualifier: "FTZ", ref_id: "" } });
    pc.ref = pc.ref || { qualifier: "FTZ", ref_id: "" };
    parts.push(h("div", { class: "ftgrid" }, fzIn("FZ13", "contact_name", pc), fzIn("FZ13", "contact_phone", pc),
      fzSelect("Reason Code (FZ13)", pc.reason, Object.entries(FTSPEC.fz.reasons).map(([k, t]) => [k, `${k} ${t}`]), (v) => { pc.reason = v; ftChanged(); draw(); }, "FZ13 pos 58-59")));
    if (["02", "03", "04", "05"].includes(pc.reason)) parts.push(h("div", { class: "ftgrid" }, fzSelect("Replacement type (FZ14)", pc.ref.qualifier, [["EN", "EN · Replacement entry number"], ["IB", "IB · Replacement in-bond number"], ["FTZ", "FTZ · Replacement admission number"]],
      (v) => { pc.ref.qualifier = v; ftChanged(); }), fzIn("FZ14", "ref_id", pc.ref)));
    parts.push(h("p", { class: "sub" }, "A request only tells CBP you intend to correct the admission. Once CBP approves it, all concurred quantities reset to zero and the admission returns to AUTHORIZED. A zone status change on the admission blocks the request."));
  }
  parts.push(ftList(a.remarks, "Remarks (FZ20, up to 60 characters each)", FTSPEC.fz.limits.remarks, null, { ph: "Discrepancy or PTT instructions" }));
  return h("div", { class: "card ftsec" }, h("div", { class: "bar" }, h("h3", { class: "grow" }, `Action ${i + 1} · ${info.title || "choose an action"}`),
    h("button", { type: "button", class: "sm danger", onclick: () => { FT.S.data.actions.splice(i, 1); ftChanged(); draw(); } }, "Remove")), ...parts);
}
function fzForm() {
  const d = FT.S.data;
  d.actions = d.actions || [];
  const root = h("div", { class: "ftform" });
  const draw = () => root.replaceChildren(
    h("p", { class: "sub" }, "FZ messages are the zone operator's follow-ups on an admission: concur with the received quantity, request or cancel a Permit To Transfer, accept or refuse the goods, report arrival, or ask for a Post Admission Correction. Each action is its own FZ10 set; up to 999 per file."),
    ...d.actions.map((a, i) => fzAction(d, a, i, draw)),
    h("button", { type: "button", class: "primary", onclick: () => { d.actions.push({ qualifier: "1", action: "H", id_number: "", containers: [], remarks: [] }); ftChanged(); draw(); } }, "+ Action (FZ10)"));
  draw();
  return root;
}
async function fzBlank() {
  const r = await post("/api/ft", { kind: "FZ", label: "New FZ filing", data: { actions: [{ qualifier: "1", action: "H", id_number: "", containers: [], remarks: [] }] } });
  await ftOpen(r.id, []);
}

/* ---------- CBP responses: paste an NF file, see what happened, update the filing ---------- */
const OUTCOME = { accepted: "ok", authorized: "ok", warning: "warn", rejected: "bad", info: "" };
function ftResultCard(r) {
  return h("div", { class: "card" }, h("div", { class: "bar" }, tag(r.outcome, OUTCOME[r.outcome]), h("b", { class: "grow" }, r.summary),
    r.admission_number ? h("code", {}, r.admission_number) : null),
    r.refs.length ? table([{ h: "Reference", f: (x) => x.qualifier }, { h: "Number", k: "id" }, { h: "Date", k: "date" }, { h: "Time", k: "time" }, { h: "FIRMS", k: "firms" }, { h: "Container", k: "container" }], r.refs) : null,
    r.messages.length ? h("ul", {}, r.messages.map((m) => h("li", {}, `${m.type_text || "Message"}${m.code ? " " + m.code : ""}: ${m.text}`))) : null,
    r.echoed && r.echoed.length ? h("details", {}, h("summary", {}, `Records CBP echoed back (${r.echoed.length}): the line(s) in error`), h("div", { class: "ftscroll" }, r.echoed.map((t) => h("div", { class: "ftline80" }, t)))) : null,
    r.filings ? h("p", { class: "sub" }, r.filings.length ? "Updated: " + r.filings.map((f) => `${f.kind} · ${f.label}`).join(", ") : "No saved filing matches this response (its admission number or bill is not in your filings). Nothing was changed.") : null);
}
function ftResponse(filingId) {
  const ta = h("textarea", { rows: 10, style: "font:12px ui-monospace,Menlo,monospace;white-space:pre", placeholder: "Paste the NF response text from your ABI software or broker (NF90, NF91, NF95 records)…" });
  const out = h("div");
  const run = async (apply) => {
    try {
      const r = await post(apply ? "/api/ft/response" : "/api/ft/decode", { text: ta.value, filing_id: filingId || undefined, kind: apply ? "nf" : "auto" });
      const warn = r.problems.length ? [h("div", { class: "alert" }, r.problems.join("; "))] : [];
      if (apply) { out.replaceChildren(...r.results.map(ftResultCard), ...warn); toast(r.matched ? "Filing status updated" : "Decoded; no filing matched"); }
      else out.replaceChildren(...r.results.map(ftResultCard), ...(r.kind === "zd" || !r.results.length ? [ftDecoded(r)] : []), ...warn);
    } catch (e) { if (e.errors) showErrors(out, e); else throw e; }
  };
  const close = modal("CBP response (NF) and broker-download copy (ZD)", h("div", {},
    h("p", { class: "sub" }, "Paste what CBP sent back. “Read only” decodes it; “Apply to filings” also records it and moves the matching filing to accepted, rejected or authorized. Only NF responses can be applied."),
    ta, h("div", { class: "bar" }, h("button", { onclick: () => run(false) }, "Read only"), h("button", { class: "primary", onclick: () => run(true) }, "Apply to filings"), h("button", { onclick: () => close() }, "Close")), out));
}
function ftDecoded(r) {
  return h("div", { class: "ftscroll" }, r.records.map((x) => h("div", {}, h("div", { class: "ftline80" }, h("b", { class: "rid" }, x.id), h("span", { class: "ftrec" }, x.text)),
    x.fields.length ? h("div", { class: "ftgrid", style: "padding:2px 8px 8px" }, x.fields.filter((f) => f.value).map((f) => h("small", {}, `${f.label} (${f.start}-${f.end}): `, h("b", {}, f.value)))) : null)));
}
async function ftResponses() {
  const rows = await get(`/api/ft/${FT.S.id}/responses`);
  modal("CBP responses for this filing", table([{ h: "When", k: "ts" }, { h: "By", k: "user" }, { h: "Outcome", f: (r) => tag(r.outcome, OUTCOME[r.outcome]) }, { h: "Disposition", k: "disposition" }, { h: "Code", k: "message_code" }, { h: "What CBP said", k: "summary" }], rows, null, "No CBP response has been recorded yet."));
}
