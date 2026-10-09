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
  const [title, rows] = [FTSPEC.maps[key].title, FTSPEC.maps[key].rows];
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
      h("details", {}, h("summary", {}, "Checks CBP performs when it receives the file (not possible here)"), h("ul", { class: "sub" }, [
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
      c.usage_map && c.usage_map !== "main" ? h("details", {}, h("summary", {}, "Show the standard map"), ftMapTable("main", c.counts)) : null);
  }
  side.replaceChildren(tabs, body);
}

/* ---------- editor shell, save, export ---------- */
async function ftSave() {
  const S = FT.S;
  const r = await act(() => post("/api/ft", { id: S.id, label: $("#ftlabel").value, admission_id: S.admission_id, data: S.data }), "Filing saved");
  if (r) { FT.S = { ...r }; FT.dirty = false; ftSide(); $("#ftstatus").replaceChildren(tag(r.status)); }
}
function ftExport() {
  const abi = FTSPEC.abi, missing = Object.entries({ "Site code": abi.site_code, "Sender ID": abi.sender_id, "Filer code": abi.filer_code, "Port code": abi.port_code }).filter(([, v]) => !v).map(([k]) => k);
  const mode = h("select", {}, h("option", { value: "batch" }, "Complete batch: A, B, FT records, Y, Z"), h("option", { value: "body" }, "Transaction records only (FT10 … FT61)"));
  const eol = h("select", {}, h("option", { value: "lf" }, "LF (Unix)"), h("option", { value: "crlf" }, "CRLF (Windows)"));
  const pw = h("input", { type: "password", maxlength: 6, autocomplete: "off", class: "up", placeholder: "6 characters" });
  const out = h("div");
  const pwBox = h("label", {}, "ABI communication password (used for this download only, never saved)", pw);
  mode.addEventListener("change", () => (pwBox.hidden = mode.value !== "batch"));
  const close = modal("Export CATAIR FT file", h("div", {},
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
      S.id && h("button", { onclick: ftHistory }, "Export history"),
      h("button", { onclick: ftSave }, "Save"), h("button", { class: "primary", onclick: async () => { await ftSave(); if (FT.S.check && FT.S.check.ok) ftExport(); else { FT.tab = "checks"; ftSide(); toast("Fix the problems listed first"); } } }, "Export…")),
    h("div", { class: "ftcols" }, ftForm(), h("div", { class: "card ftsidebox", id: "ftside" })));
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
  modal("CATAIR FT usage maps", h("div", {}, h("p", { class: "sub" }, `From ${FTSPEC.source.ft.title}, version ${FTSPEC.source.ft.version} (${FTSPEC.source.ft.date}), Pub # ${FTSPEC.source.ft.pub}. Loops nest: a conveyance (up to 999) holds bills (up to 9,999), and each bill holds its HTS lines (up to 9,999).`),
    ["main", "T", "S", "D"].map((k) => ftMapTable(k, null))));
}

VIEWS.catair = async () => {
  if (!FTSPEC) FTSPEC = await get("/api/ft/spec");
  if (FT.current != null && FT.S) return ftEditor();
  const list = await get("/api/ft");
  return h("div", {},
    head("CATAIR FT Filing", `Builds the e214 FT (Input) records exactly as the ACE CATAIR Foreign Trade Zone chapter (v${FTSPEC.source.ft.version}, ${FTSPEC.source.ft.date}) defines them: 80-character fixed-width records in the usage-map structure. It prepares the file; it does not transmit it to CBP.`,
      h("button", { onclick: ftUsageMaps }, "Usage map"), h("button", { onclick: ftFromAdmission }, "Build from an e214…"), h("button", { class: "primary", onclick: ftBlank }, "+ Blank filing")),
    h("div", { class: "card" }, table([{ h: "Admission no.", k: "admission_number" }, { h: "Label", k: "label" }, { h: "Action", f: (r) => ({ A: "Add", D: "Delete", R: "Replace", S: "Status change" })[r.action] || r.action || "" },
      { h: "Status", f: (r) => tag(r.status) }, { h: "Updated", k: "updated_at" }, { h: "By", k: "updated_by" }], list, (r) => ftOpen(r.id), "No filings yet. Build one from an e214 admission, or start a blank filing.")));
};
