/**
 * NYC Property Intel — watch double-opt-in confirmation (/watch-confirm?t=<token>).
 *
 * Reads the token from ?t=, POSTs it to the Railway API to confirm every watch
 * for that email, and shows the result. Auth-free by design (the token is an
 * unguessable per-registration slug).
 */
(function () {
  "use strict";

  var API_BASE = "https://nyc-property-intel-production.up.railway.app";
  var titleEl = document.getElementById("confirm-title");
  var statusEl = document.getElementById("confirm-status");

  function show(title, html) {
    if (titleEl) titleEl.textContent = title;
    if (statusEl) statusEl.innerHTML = html;
  }

  function init() {
    var token = new URLSearchParams(window.location.search).get("t");
    if (!token || !/^[A-Za-z0-9_-]{6,32}$/.test(token)) {
      show("Invalid link", "<p>This confirmation link is invalid or incomplete.</p>" +
        '<p><a href="/chat" class="btn btn-primary">Run a free report &rarr;</a></p>');
      return;
    }

    fetch(API_BASE + "/api/watch/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: token }),
    })
      .then(function (res) {
        if (res.ok) {
          show("You're all set ✓",
            "<p>Your building alerts are confirmed. We'll email you when a " +
            "watched building gets a new violation, litigation, or lien — and " +
            "only then.</p>" +
            '<p><a href="/chat" class="btn btn-primary">Check another building &rarr;</a></p>');
          if (typeof posthog !== "undefined") posthog.capture("watch_confirmed");
        } else if (res.status === 404) {
          show("Link expired",
            "<p>This confirmation link doesn't match an active request. It may " +
            "have already been used.</p>" +
            '<p><a href="/chat" class="btn btn-primary">Run a free report &rarr;</a></p>');
        } else {
          throw new Error("unavailable");
        }
      })
      .catch(function () {
        show("Something went wrong",
          "<p>We couldn't confirm right now. Please try the link again in a moment.</p>");
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
