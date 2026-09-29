"use strict";
/* Front page + reset page behaviour. Text is always set with textContent. */
const $ = (s) => document.querySelector(s);

async function post(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({ errors: ["unexpected response"] }));
  if (!r.ok) throw new Error((data.errors || ["request failed"]).join(" · "));
  return data;
}
function say(el, text, kind) { el.textContent = text; el.className = "msg " + (kind || ""); }
function busy(form, on) { form.querySelectorAll("button[type=submit]").forEach((b) => (b.disabled = on)); }

document.querySelectorAll("[data-toggle]").forEach((b) => b.addEventListener("click", () => {
  const i = document.getElementById(b.dataset.toggle);
  i.type = i.type === "password" ? "text" : "password";
  b.textContent = i.type === "password" ? "Show" : "Hide";
}));

if ($("#login-form")) {
  fetch("/auth/config").then((r) => r.json()).then((c) => {
    if (c.demo) {
      $("#demo-user").textContent = c.demo.username;
      $("#demo-pass").textContent = c.demo.password;
      $("#demo").hidden = false;
      $("#use-demo").addEventListener("click", () => {
        $("#username").value = c.demo.username; $("#password").value = c.demo.password; $("#login-form button[type=submit]").focus();
      });
    }
    $("#forgot-hint").textContent = c.email_reset
      ? "Enter your user name or email. We will email a reset link that works once, for 30 minutes."
      : "Enter your user name. Email is not set up on this server, so the reset link goes to the site administrator, who can pass it on. It works once, for 30 minutes.";
  }).catch(() => {});

  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault(); const f = e.target; busy(f, true); say($("#login-msg"), "");
    try { await post("/auth/login", { username: f.username.value, password: f.password.value }); location.href = "/"; }
    catch (err) { say($("#login-msg"), err.message, "err"); busy(f, false); f.password.select(); }
  });
  const show = (forgot) => { $("#signin").hidden = forgot; $("#forgot").hidden = !forgot; (forgot ? $("#identifier") : $("#username")).focus(); };
  $("#show-forgot").addEventListener("click", (e) => { e.preventDefault(); $("#identifier").value = $("#username").value; show(true); });
  $("#show-signin").addEventListener("click", (e) => { e.preventDefault(); show(false); });
  $("#forgot-form").addEventListener("submit", async (e) => {
    e.preventDefault(); const f = e.target; busy(f, true); say($("#forgot-msg"), "");
    try { const r = await post("/auth/forgot", { identifier: f.identifier.value }); say($("#forgot-msg"), r.message, "ok"); }
    catch (err) { say($("#forgot-msg"), err.message, "err"); }
    busy(f, false);
  });
}

if ($("#reset-form")) {
  const token = new URLSearchParams(location.search).get("token") || "";
  history.replaceState(null, "", "/reset");   // keep the token out of the address bar and history
  $("#reset-form").addEventListener("submit", async (e) => {
    e.preventDefault(); const f = e.target; say($("#reset-msg"), "");
    if (!token) return say($("#reset-msg"), "This page needs the link from your reset email. Request a new link from the sign-in page.", "err");
    if (f.password.value !== f.confirm.value) return say($("#reset-msg"), "The two passwords do not match.", "err");
    busy(f, true);
    try {
      const r = await post("/auth/reset", { token, password: f.password.value });
      say($("#reset-msg"), r.message, "ok"); f.reset(); setTimeout(() => (location.href = "/"), 1800);
    } catch (err) { say($("#reset-msg"), err.message, "err"); busy(f, false); }
  });
}
