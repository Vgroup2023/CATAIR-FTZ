"use strict";
/* Registers the service worker and powers the "Install app" buttons on every page. */
(() => {
  if ("serviceWorker" in navigator) addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
  const standalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  let offer = null;
  const buttons = () => document.querySelectorAll("[data-install]");
  const refresh = () => buttons().forEach((b) => { b.hidden = standalone() || !(offer || ios); });
  addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); offer = e; refresh(); });
  addEventListener("appinstalled", () => { offer = null; refresh(); });
  addEventListener("DOMContentLoaded", () => {
    refresh();
    buttons().forEach((b) => b.addEventListener("click", async () => {
      if (offer) { offer.prompt(); await offer.userChoice.catch(() => {}); offer = null; refresh(); }
      else if (ios) alert("To install on iPhone or iPad:\n1. Open this page in Safari.\n2. Tap the Share button.\n3. Choose \"Add to Home Screen\", then Add.");
    }));
  });
})();
