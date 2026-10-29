"use strict";
/* Training & SOP tab. The content comes from /api/training (the same source as the downloadable .odt guide).
   Progress ticks are kept in this browser only (localStorage), per user. Uses the helpers from app.js (h, get, head, tag, ME). */

const TRN = { data: null, done: new Set(), open: null };
const trnKey = () => "ftz-training-" + ((typeof ME === "object" && ME && ME.username) || "user");
function trnLoad() { try { TRN.done = new Set(JSON.parse(localStorage.getItem(trnKey()) || "[]")); } catch { TRN.done = new Set(); } }
function trnSave() { try { localStorage.setItem(trnKey(), JSON.stringify([...TRN.done])); } catch { /* private window: progress just isn't remembered */ } }

function trnInline(text) {          // **bold** and `code` -> DOM nodes (never innerHTML)
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/).filter(Boolean).map((p) =>
    p.startsWith("**") && p.endsWith("**") && p.length > 4 ? h("b", {}, p.slice(2, -2))
      : p.startsWith("`") && p.endsWith("`") && p.length > 2 ? h("code", {}, p.slice(1, -1)) : p);
}
function trnBlock(b) {
  switch (b.t) {
    case "p": return h("p", {}, trnInline(b.text));
    case "h3": return h("h3", {}, trnInline(b.text));
    case "ul": return h("ul", { class: "trnlist" }, b.items.map((i) => h("li", {}, trnInline(i))));
    case "ol": return h("ol", { class: "trnlist" }, b.items.map((i) => h("li", {}, trnInline(i))));
    case "check": return h("div", { class: "trnchk" }, b.items.map((i) => h("label", {}, h("input", { type: "checkbox" }), " ", trnInline(i))));
    case "note": return h("div", { class: "trnnote " + b.kind }, h("b", {}, { tip: "Tip: ", warn: "Important: ", info: "Note: " }[b.kind]), trnInline(b.text));
    case "table": return h("div", { class: "ftscroll" }, h("table", { class: "trntable" }, h("tr", {}, b.head.map((c) => h("th", {}, c))),
      b.rows.map((r) => h("tr", {}, r.map((c) => h("td", {}, trnInline(c)))))));
    default: return null;
  }
}

VIEWS.training = async () => {
  TRN.data = TRN.data || await get("/api/training");
  trnLoad();
  const g = TRN.data;
  const pane = h("div", { class: "card trnpane" });
  const tocBox = h("div", { class: "card trntoc" });
  const drawToc = () => {
    const pct = Math.round(100 * TRN.done.size / g.sections.length);
    tocBox.replaceChildren(h("b", {}, `Your progress: ${TRN.done.size} of ${g.sections.length}`),
      h("div", { class: "trnbar" }, h("span", { style: `width:${pct}%` })),
      ...g.sections.map((s) => h("a", { class: "trnlink" + (TRN.open === s.id ? " on" : ""), onclick: () => show(s.id) },
        TRN.done.has(s.id) ? "✓ " : "", s.title)));
  };
  const show = (id) => {
    TRN.open = id;
    const s = g.sections.find((x) => x.id === id) || g.sections[0];
    const i = g.sections.indexOf(s);
    const done = h("input", { type: "checkbox", checked: TRN.done.has(s.id), onchange: (e) => { e.target.checked ? TRN.done.add(s.id) : TRN.done.delete(s.id); trnSave(); drawToc(); } });
    pane.replaceChildren(h("h2", { style: "margin-top:0" }, s.title), h("p", { class: "sub" }, `For: ${s.audience} · about ${s.minutes} minutes`),
      ...s.blocks.map(trnBlock).filter(Boolean),
      h("div", { class: "bar" }, h("label", {}, done, " I have read and understood this section"), h("span", { class: "grow" }),
        i > 0 ? h("button", { onclick: () => show(g.sections[i - 1].id) }, "← Previous") : null,
        i < g.sections.length - 1 ? h("button", { class: "primary", onclick: () => show(g.sections[i + 1].id) }, "Next →") : null));
    drawToc();
    window.scrollTo({ top: 0 });
  };
  show(TRN.open || g.sections[0].id);
  return h("div", {}, head("Training & SOP", `How to use FTZ Control: a guide for new users and the standard procedures for each task. Version ${g.version}, ${g.date}.`,
    h("a", { class: "btn primary", href: "/training/guide.odt", download: "FTZ-Control-Training-and-SOP.odt" }, "Download guide (.odt)"),
    h("button", { onclick: () => window.print() }, "Print this section")),
    h("p", { class: "sub" }, "The .odt file opens in Word, LibreOffice, Google Docs and Pages. It contains every section, the checklists and a training record to sign."),
    h("div", { class: "trncols" }, tocBox, pane));
};
