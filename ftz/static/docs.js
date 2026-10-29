"use strict";
/* Supporting documents: attach, list, download and void files on a record, plus the Documents screen.
   Uses the helpers from app.js (h, get, post, act, modal, tag, table, head, toast). Files travel as base64 JSON and are fingerprinted (SHA-256) on the server. */

const DOCS = { spec: null };
const docKb = (n) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`;
const docWho = (d) => `${d.uploaded_by || ""} · ${(d.uploaded_at || "").replace("T", " ")}`;
const canEditDocs = () => ME && ME.role !== "auditor";

async function docDownload(d) {
  const r = await fetch(`/api/documents/${d.id}/download`);
  if (!r.ok) { const j = await r.json().catch(() => ({})); alert((j.errors || ["Download failed"]).join("\n")); return; }
  const blob = await r.blob();
  const a = h("a", { href: URL.createObjectURL(blob), download: d.filename });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}
function docRead(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result).split(",")[1] || "");
    fr.onerror = () => reject(new Error("The file could not be read"));
    fr.readAsDataURL(file);
  });
}
async function docUploadForm(entity, entityId, after) {
  DOCS.spec = DOCS.spec || await get("/api/documents/spec");
  const sp = DOCS.spec;
  const file = h("input", { type: "file", accept: sp.types.map((t) => "." + t).join(",") });
  const kind = h("select", {}, sp.kinds.map((k) => h("option", { value: k }, k.replace("_", " "))));
  const note = h("input", { maxlength: 300, placeholder: "Optional note, e.g. commercial invoice 4471" });
  const out = h("div");
  const close = modal("Attach a document", h("div", {},
    h("p", { class: "sub" }, `PDF, images, CSV/TXT, Word or Excel, up to ${sp.max_bytes / 1048576} MB. The file is fingerprinted when stored and can be voided later but never deleted.`),
    h("div", { class: "fields" }, h("label", { class: "wide" }, "File", file), h("label", {}, "Type", kind), h("label", { class: "wide" }, "Note", note)), out,
    h("div", { class: "bar" }, h("button", { class: "primary", onclick: async (ev) => {
      const f = file.files[0];
      if (!f) { out.replaceChildren(h("div", { class: "errs" }, "Choose a file first")); return; }
      if (f.size > sp.max_bytes) { out.replaceChildren(h("div", { class: "errs" }, `That file is ${docKb(f.size)}; the limit is ${sp.max_bytes / 1048576} MB`)); return; }
      ev.target.disabled = true;
      try {
        const r = await post("/api/documents", { entity, entity_id: entityId, filename: f.name, kind: kind.value, note: note.value, data: await docRead(f) });
        toast(r.duplicate ? "That exact file is already attached" : "Document stored");
        close(); after && after();
      } catch (e) { ev.target.disabled = false; out.replaceChildren(h("div", { class: "errs" }, (e.errors || [String(e)]).join("; "))); }
    } }, "Upload"), h("button", { onclick: () => close() }, "Cancel"))), { narrow: true });
}
function docVoid(d, after) {
  const reason = prompt(`Void "${d.filename}"? It stays on record. Why is it being voided?`);
  if (reason === null) return;
  act(() => post(`/api/documents/${d.id}/void`, { reason }), "Document voided").then((r) => r && after && after());
}
function docRows(list, after, showRecord) {
  return table([
    { h: "File", f: (d) => h("a", { href: "#", onclick: (e) => { e.preventDefault(); e.stopPropagation(); docDownload(d); } }, d.filename) },
    showRecord ? { h: "Belongs to", f: (d) => d.entity === "general" ? "General" : `${d.entity} #${d.entity_id}` } : null,
    { h: "Type", f: (d) => d.kind ? d.kind.replace("_", " ") : "" }, { h: "Size", f: (d) => docKb(d.size), num: true },
    { h: "Added", f: docWho }, { h: "Note", k: "note" },
    { h: "Fingerprint", f: (d) => h("code", { title: d.sha256 }, d.sha256.slice(0, 12) + "…") },
    { h: "", f: (d) => d.voided_at ? tag("voided", "bad") : (canEditDocs() ? h("button", { class: "sm danger", onclick: (e) => { e.stopPropagation(); docVoid(d, after); } }, "Void") : "") },
  ].filter(Boolean), list, null, "No documents attached yet.");
}
/* a panel to drop into any record's detail window */
function docsPanel(entity, entityId) {
  const box = h("div", { class: "card" });
  const draw = async () => {
    let list = [];
    try { list = await get(`/api/documents?entity=${entity}&entity_id=${entityId}`); } catch (e) { box.replaceChildren(h("div", { class: "errs" }, (e.errors || [String(e)]).join("; "))); return; }
    box.replaceChildren(h("div", { class: "bar" }, h("b", { class: "grow" }, `Documents (${list.filter((d) => !d.voided_at).length})`),
      canEditDocs() ? h("button", { class: "sm primary", onclick: () => docUploadForm(entity, entityId, draw) }, "+ Attach") : null), docRows(list, draw, false));
  };
  draw();
  return box;
}

VIEWS.documents = async () => {
  let q = "";
  const body = h("div");
  const draw = async () => {
    const list = await get("/api/documents" + (q ? `?q=${encodeURIComponent(q)}` : ""));
    body.replaceChildren(h("div", { class: "card" }, docRows(list, draw, true)));
  };
  const search = h("input", { placeholder: "Search file name, note or type", style: "max-width:320px", oninput: (e) => { q = e.target.value.trim(); clearTimeout(search._t); search._t = setTimeout(draw, 300); } });
  await draw();
  return h("div", {}, head("Documents", "Supporting files stored with the records they belong to. Open any e214, e216 or in-bond record to attach one. Files are fingerprinted, never deleted, and every download is logged.",
    search, canEditDocs() ? h("button", { class: "primary", onclick: () => docUploadForm("general", 0, draw) }, "+ General document") : null), body);
};
