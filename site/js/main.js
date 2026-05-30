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
    var signupBtn = signupForm.querySelector(".signup-btn");

    // ── Turnstile gating ────────────────────────────────────────────
    // The submit button is `disabled` in HTML by default; we enable it
    // only when Turnstile has issued a token. This closes the race the
    // first-test session hit: button clickable, Turnstile widget visually
    // "Success!", but `getResponse()` returns "" (token consumed by an
    // earlier in-flight attempt OR widget reset still pending). Submitting
    // an empty token gets 403 from the backend, which UX-rendered as the
    // misleading "Something went wrong" fallback.
    //
    // Polling instead of relying on the `data-callback` attribute because
    // Turnstile's script tag uses `async`, so it may run BEFORE main.js
    // installs callbacks on `window` — auto-render would silently no-op.
    // Polling getResponse() works regardless of script load order.
    var turnstilePollId = null;
    var turnstileTimeoutId = null;
    var TURNSTILE_POLL_MS = 500;
    var TURNSTILE_FALLBACK_MS = 15000;

    function turnstileGateButton() {
      if (turnstilePollId) clearInterval(turnstilePollId);
      if (turnstileTimeoutId) clearTimeout(turnstileTimeoutId);
      signupBtn.disabled = true;

      turnstilePollId = setInterval(function () {
        if (typeof window.turnstile === "undefined") return;
        if (typeof window.turnstile.getResponse !== "function") return;
        var t = "";
        try { t = window.turnstile.getResponse() || ""; } catch (e) { /* ignore */ }
        if (t) {
          clearInterval(turnstilePollId);
          clearTimeout(turnstileTimeoutId);
          turnstilePollId = null;
          turnstileTimeoutId = null;
          signupBtn.disabled = false;
        }
      }, TURNSTILE_POLL_MS);

      // Fallback: if Turnstile never issues a token (ad blocker, network
      // failure, script blocked by CSP, etc.), un-gate the button after
      // 15s so the user gets a clear "Bot check failed" response from the
      // backend instead of a permanently dead form.
      turnstileTimeoutId = setTimeout(function () {
        if (turnstilePollId) clearInterval(turnstilePollId);
        turnstilePollId = null;
        turnstileTimeoutId = null;
        signupBtn.disabled = false;
      }, TURNSTILE_FALLBACK_MS);
    }

    // Start the initial gate as soon as main.js runs.
    turnstileGateButton();

    signupForm.addEventListener("submit", function (e) {
      e.preventDefault();

      var email = signupForm.querySelector('input[name="email"]').value.trim();
      var btn = signupBtn;

      signupError.textContent = "";

      if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        signupError.textContent = "Please enter a valid email address.";
        return;
      }

      // Defense in depth: even if the button gate failed (e.g., fallback
      // timeout fired), don't ship an empty token to the backend — that's
      // a guaranteed 403 with the misleading "Something went wrong".
      var turnstileToken = "";
      if (typeof window.turnstile !== "undefined" && typeof window.turnstile.getResponse === "function") {
        try { turnstileToken = window.turnstile.getResponse() || ""; } catch (err) { turnstileToken = ""; }
      }
      if (!turnstileToken) {
        signupError.textContent =
          "Please wait a moment for verification to complete, then try again.";
        turnstileGateButton();
        return;
      }

      btn.disabled = true;
      btn.textContent = "Sending…";

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
          btn.textContent = SIGNUP_BTN_DEFAULT_LABEL;
          // Reset Turnstile — used tokens can't be re-submitted, and a
          // failed challenge needs a fresh widget render. Safe to call
          // even when the widget isn't present.
          if (typeof window.turnstile !== "undefined" && typeof window.turnstile.reset === "function") {
            try { window.turnstile.reset(); } catch (e) { /* no-op */ }
          }
          // Re-gate the button: poll until a fresh token is available.
          // This is the critical fix — the previous version re-enabled
          // the button immediately on error, allowing a second click
          // before Turnstile's reset() had issued a new token.
          turnstileGateButton();
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
