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

  function ph(event, props) {
    if (window.posthog) window.posthog.capture(event, props || {});
  }

  function activateTab(btn) {
    var target = btn.getAttribute("data-tab");
    ph("install_tab_viewed", { tab: target });
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
  // POSTs to our Railway backend `/api/signup` instead of the public
  // Loops form ID directly. The backend runs the same anti-bot checks
  // as the Loops webhook (disposable / MX / brand-prefix), then issues
  // a token + magic link via the chat-flow primitives.
  // See docs/signup-rebuild-plan-2026-05-06.md.
  //
  var SIGNUP_ENDPOINT =
    "https://nyc-property-intel-production.up.railway.app/api/signup";
  var SIGNUP_BTN_DEFAULT_LABEL = "Email me a token";

  var signupForm = document.getElementById("hero-signup-form");
  var signupSuccess = document.getElementById("hero-signup-success");
  var signupError = document.getElementById("hero-signup-error");

  if (signupForm) {
    signupForm.addEventListener("submit", function (e) {
      e.preventDefault();

      var email = signupForm.querySelector('input[name="email"]').value.trim();
      var btn = signupForm.querySelector(".signup-btn");

      signupError.textContent = "";

      if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        signupError.textContent = "Please enter a valid email address.";
        return;
      }

      btn.disabled = true;
      btn.textContent = "Sending…";

      // Cloudflare Turnstile token (optional client-side; backend only
      // enforces when SIGNUP_REQUIRE_TURNSTILE=true). getResponse returns
      // an empty string when the widget isn't loaded / hasn't rendered;
      // backend treats that as an invalid token when enforcement is on.
      var turnstileToken = "";
      if (typeof window.turnstile !== "undefined" && typeof window.turnstile.getResponse === "function") {
        try {
          turnstileToken = window.turnstile.getResponse() || "";
        } catch (e) {
          turnstileToken = "";
        }
      }

      // POST as JSON — matches the backend handler's contract.
      // The handler accepts:
      //   { email, hp_field?, started_at_ms?, turnstile_token? }
      // hp_field is the future honeypot (always absent from real users).
      // started_at_ms is the future time-on-form check (Phase D).
      fetch(SIGNUP_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email, turnstile_token: turnstileToken }),
      })
        .then(function (res) {
          if (res.status === 429) throw new Error("rate_limited");
          if (res.status === 403) throw new Error("verification_failed");
          if (!res.ok) throw new Error("server_error");
          return res.json();
        })
        .then(function (data) {
          // Backend contract: success returns {"ok": true}. Anti-bot
          // rejections (disposable / no-MX / heuristic) ALSO return 200
          // {"ok": true} silently — that's intentional so bots can't
          // oracle the outcome. From the user's perspective, success or
          // quiet rejection both show "Check your inbox", which is fine:
          // a real user who happens to type a disposable address just
          // won't get an email and will retry with their real one.
          if (data && data.ok === true) {
            ph("signup_submitted", { source: "api_signup" });
            signupForm.hidden = true;
            signupSuccess.hidden = false;
          } else {
            throw new Error("unknown_error");
          }
        })
        .catch(function (err) {
          btn.disabled = false;
          btn.textContent = SIGNUP_BTN_DEFAULT_LABEL;
          // Reset Turnstile so the user can retry — a used token can't be
          // re-submitted, and an expired/failed challenge needs a fresh
          // widget render. Safe to call even when the widget isn't present.
          if (typeof window.turnstile !== "undefined" && typeof window.turnstile.reset === "function") {
            try { window.turnstile.reset(); } catch (e) { /* no-op */ }
          }
          if (err.message === "rate_limited") {
            signupError.textContent =
              "Too many signups from this address. Try again in an hour.";
          } else if (err.message === "verification_failed") {
            signupError.textContent =
              "Bot check failed. Please complete the verification and try again.";
          } else if (err.message === "server_error") {
            signupError.textContent =
              "Something went wrong. Try again in a moment.";
          } else {
            signupError.textContent = "Could not sign up. Please try again.";
          }
        });
    });
  }

  // ── CTA click tracking ─────────────────────────────────────────
  document.querySelectorAll('a[href*="github.com"]').forEach(function (el) {
    el.addEventListener("click", function () {
      ph("cta_clicked", { label: "github", href: el.getAttribute("href") });
    });
  });
  // The hero form anchor was renamed from #hero-signup-form to
  // #hero-signup-form-wrapper when the form was moved inside a <details>
  // collapsible. The CTA below scrolls there AND opens the details so
  // the form is visible without a second click.
  var getAccessBtn = document.querySelector(
    'a[href="#hero-signup-form-wrapper"]'
  );
  if (getAccessBtn) {
    getAccessBtn.addEventListener("click", function () {
      ph("cta_clicked", { label: "get_access_nav" });
      var wrapper = document.getElementById("hero-signup-form-wrapper");
      if (wrapper) wrapper.open = true;
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

  // ── Auto-expand <details> when navigated via anchor link ──────
  // Moved out of inline <script> in index.html so it isn't blocked
  // by the CSP (script-src 'self' …; no 'unsafe-inline').
  function openDetailsForHash() {
    var hash = window.location.hash;
    if (!hash || hash === "#") return;
    var target;
    try { target = document.querySelector(hash); } catch (e) { return; }
    if (!target) return;
    if (target.tagName === "DETAILS" && !target.open) {
      target.open = true;
    } else {
      var details = target.closest && target.closest("details");
      if (details && !details.open) details.open = true;
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", openDetailsForHash);
  } else {
    openDetailsForHash();
  }
  window.addEventListener("hashchange", openDetailsForHash);
  document.addEventListener("click", function (e) {
    var link = e.target.closest && e.target.closest('a[href^="#"]');
    if (!link) return;
    var href = link.getAttribute("href");
    if (!href || href === "#") return;
    var target;
    try { target = document.querySelector(href); } catch (err) { return; }
    if (!target) return;
    if (target.tagName === "DETAILS" && !target.open) {
      target.open = true;
    }
  });
})();
