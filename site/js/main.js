// ═══════════════════════════════════════════════════════════════════
// NYC Property Intel — Landing Page JS
// ═══════════════════════════════════════════════════════════════════

(function () {
  "use strict";

  // ── Mobile nav toggle ──────────────────────────────────────────
  const toggle = document.querySelector(".nav-toggle");
  const links = document.querySelector(".nav-links");

  if (toggle && links) {
    toggle.addEventListener("click", function () {
      const expanded = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", String(!expanded));
      links.classList.toggle("open");
    });

    // Close nav when a link is clicked
    links.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", function () {
        links.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
      });
    });
  }

  // ── Install tabs ───────────────────────────────────────────────
  const tabBtns = document.querySelectorAll(".tab-btn");
  const tabContents = document.querySelectorAll(".tab-content");

  function activateTab(btn) {
    var target = btn.getAttribute("data-tab");
    tabBtns.forEach(function (b) {
      b.classList.remove("active");
      b.setAttribute("aria-selected", "false");
      b.setAttribute("tabindex", "-1");
    });
    tabContents.forEach(function (c) { c.classList.remove("active"); });
    btn.classList.add("active");
    btn.setAttribute("aria-selected", "true");
    btn.setAttribute("tabindex", "0");
    btn.focus();
    var el = document.getElementById(target);
    if (el) el.classList.add("active");
  }

  tabBtns.forEach(function (btn) {
    btn.addEventListener("click", function () { activateTab(btn); });
    btn.addEventListener("keydown", function (e) {
      var idx = Array.prototype.indexOf.call(tabBtns, btn);
      if (e.key === "ArrowRight") {
        e.preventDefault();
        activateTab(tabBtns[(idx + 1) % tabBtns.length]);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        activateTab(tabBtns[(idx - 1 + tabBtns.length) % tabBtns.length]);
      }
    });
  });

  // ── Smooth scroll for anchor links ─────────────────────────────
  document.querySelectorAll('a[href^="#"]').forEach(function (anchor) {
    anchor.addEventListener("click", function (e) {
      var targetId = this.getAttribute("href");
      if (targetId === "#") return;

      var target = document.querySelector(targetId);
      if (target) {
        e.preventDefault();
        target.scrollIntoView({ behavior: "smooth", block: "start" });
        // Update URL without jumping
        history.pushState(null, "", targetId);
      }
    });
  });
})();
