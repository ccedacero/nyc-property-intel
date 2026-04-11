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

  // ── Email signup form ───────────────────────────────────────────
  //
  // Uses Loops.so newsletter form API.
  // Setup: create a free account at loops.so, create an Audience form,
  // copy your Form ID, and paste it below as LOOPS_FORM_ID.
  //
  var LOOPS_FORM_ID = "cmntqdkqy00y20iycvyyxby0m";

  var signupForm = document.getElementById("hero-signup-form");
  var signupSuccess = document.getElementById("hero-signup-success");
  var signupError = document.getElementById("hero-signup-error");

  if (signupForm) {
    signupForm.addEventListener("submit", function (e) {
      e.preventDefault();

      var email = signupForm.querySelector('input[name="email"]').value.trim();
      var role = signupForm.querySelector('select[name="role"]').value;
      var btn = signupForm.querySelector(".signup-btn");

      signupError.textContent = "";

      if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        signupError.textContent = "Please enter a valid email address.";
        return;
      }

      btn.disabled = true;
      btn.textContent = "Sending\u2026";

      var body = new URLSearchParams({ email: email });
      if (role) body.append("userGroup", role);

      fetch("https://app.loops.so/api/newsletter-form/" + LOOPS_FORM_ID, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString(),
      })
        .then(function (res) {
          if (!res.ok) throw new Error("server_error");
          return res.json();
        })
        .then(function (data) {
          if (data.success) {
            signupForm.hidden = true;
            signupSuccess.hidden = false;
          } else {
            throw new Error(data.message || "unknown_error");
          }
        })
        .catch(function (err) {
          btn.disabled = false;
          btn.textContent = "Get Early Access";
          if (err.message === "server_error") {
            signupError.textContent = "Something went wrong. Try again in a moment.";
          } else {
            signupError.textContent = "Could not sign up. Please try again.";
          }
        });
    });
  }

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
