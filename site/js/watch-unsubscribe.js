/**
 * NYC Property Intel — watch unsubscribe (/watch-unsubscribe?t=<token>[&all=1]).
 *
 * Reads the token from ?t= (the watch row id, linked from every alert email)
 * and shows a CONFIRM BUTTON first — nothing is mutated on page load, because
 * corporate mail scanners (Safe Links, Proofpoint, etc.) execute JS when they
 * detonate links and would silently unsubscribe recipients otherwise
 * (RFC 8058: opt-out must be an explicit POST). The click POSTs to the
 * Railway API to deactivate that watch — or every watch for the owning email
 * with &all=1. Auth-free by design (the token is an unguessable per-watch
 * slug that only travels in that subscriber's own emails).
 */
(function () {
  "use strict";

  var API_BASE = "https://nyc-property-intel-production.up.railway.app";
  var titleEl = document.getElementById("unsubscribe-title");
  var statusEl = document.getElementById("unsubscribe-status");

  function show(title, html) {
    if (titleEl) titleEl.textContent = title;
    if (statusEl) statusEl.innerHTML = html;
  }

  function unsubscribe(token, all, done) {
    fetch(API_BASE + "/api/watch/unsubscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: token, all: all }),
    })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (d) {
          if (res.ok) {
            done(null, d);
            if (typeof posthog !== "undefined") {
              posthog.capture("watch_unsubscribed", { all: all });
            }
          } else if (res.status === 404) {
            done("not_found", d);
          } else {
            done("unavailable", d);
          }
        });
      })
      .catch(function () { done("unavailable", {}); });
  }

  function showDone(all, address) {
    var what = all
      ? "every building alert for your email"
      : "alerts for " + (address ? escapeText(address) : "this building");
    show(all ? "All alerts stopped ✓" : "You're unsubscribed ✓",
      "<p>You won't get " + what + " anymore. You can re-subscribe any time " +
      "from a report page or the chat.</p>" +
      '<p><a href="/chat" class="btn btn-primary">Run a free report &rarr;</a></p>');
  }

  // Text-safe interpolation for the server-supplied address.
  function escapeText(s) {
    var div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  function init() {
    var params = new URLSearchParams(window.location.search);
    var token = params.get("t");
    var all = params.get("all") === "1";
    if (!token || !/^[A-Za-z0-9_-]{6,32}$/.test(token)) {
      show("Link incomplete",
        "<p>This unsubscribe link is missing its token. Open the unsubscribe " +
        "link from your most recent alert email — or reply to any alert " +
        "email and we'll take you off manually.</p>" +
        '<p><a href="/chat" class="btn btn-primary">Run a free report &rarr;</a></p>');
      return;
    }

    // Explicit-intent confirm step — no mutation until the user clicks.
    show(all ? "Stop all building alerts?" : "Stop alerts for this building?",
      "<p>" + (all
        ? "This turns off <strong>every</strong> building alert for your email."
        : "This stops alerts for the building in the email that brought you " +
          "here. Your other watched buildings (if any) are unaffected.") + "</p>" +
      '<p><button type="button" class="btn btn-primary" id="unsub-confirm-btn">' +
      (all ? "Stop all alerts" : "Unsubscribe") + "</button></p>" +
      (all ? "" :
        '<p><button type="button" class="btn btn-outline" id="unsub-all-btn">' +
        "Stop ALL my building alerts instead</button></p>"));

    var confirmBtn = document.getElementById("unsub-confirm-btn");
    var allBtn = document.getElementById("unsub-all-btn");

    function run(unsubAll, btn) {
      btn.disabled = true;
      unsubscribe(token, unsubAll, function (err, d) {
        if (err === "not_found") {
          show("Link not recognized",
            "<p>This unsubscribe link doesn't match an active watch. You may " +
            "already be unsubscribed.</p>" +
            '<p><a href="/chat" class="btn btn-primary">Run a free report &rarr;</a></p>');
          return;
        }
        if (err) {
          btn.disabled = false;
          show("Something went wrong",
            "<p>We couldn't update your alerts right now. Please try again in " +
            "a moment.</p>" +
            '<p><button type="button" class="btn btn-primary" id="unsub-retry-btn">Try again</button></p>');
          var retry = document.getElementById("unsub-retry-btn");
          if (retry) retry.addEventListener("click", function () { run(unsubAll, retry); });
          return;
        }
        showDone(unsubAll, d && d.address);
      });
    }

    if (confirmBtn) confirmBtn.addEventListener("click", function () { run(all, confirmBtn); });
    if (allBtn) allBtn.addEventListener("click", function () { run(true, allBtn); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
