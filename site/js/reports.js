/**
 * NYC Property Intel — "Your Reports" history (/reports).
 *
 * The retention surface (migration 016 / GTM Phase 0 §3b #6). Reads the trial
 * token from localStorage (same key the chat uses), fetches the caller's saved
 * reports from the Railway API, and renders them newest-first as links to the
 * existing /r/<id> permalink viewer.
 *
 * Authenticated-only by design: anonymous free-tier users have no stable
 * identity, so they see a "create a free account" prompt instead.
 *
 * All report-derived strings (address, query) are user-influenced, so every
 * value is inserted via textContent / DOM nodes — never innerHTML.
 */
(function () {
  "use strict";

  var API_BASE = "https://nyc-property-intel-production.up.railway.app";
  var TOKEN_KEY = "nyc_pi_token";

  var statusEl = document.getElementById("reports-status");
  var signedOutEl = document.getElementById("reports-signedout");
  var listEl = document.getElementById("reports-list");
  var emptyEl = document.getElementById("reports-empty");
  var ctaEl = document.getElementById("reports-cta");
  var metaEl = document.getElementById("reports-meta");

  function show(el) { if (el) el.hidden = false; }
  function hide(el) { if (el) el.hidden = true; }

  function capture(event, props) {
    if (typeof posthog !== "undefined") {
      try { posthog.capture(event, props || {}); } catch (e) { /* no-op */ }
    }
  }

  // "Jun 19, 2026" — stable, locale-light, no time-of-day noise.
  function formatDate(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    try {
      return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
    } catch (e) {
      return d.toISOString().slice(0, 10);
    }
  }

  function renderList(reports) {
    listEl.textContent = "";
    reports.forEach(function (r) {
      var li = document.createElement("li");
      li.className = "reports-item";

      var a = document.createElement("a");
      a.className = "reports-item-link";
      a.href = r.url || ("/r/" + r.id);
      a.addEventListener("click", function () {
        capture("reports_history_open", { id: r.id || null });
      });

      var title = document.createElement("span");
      title.className = "reports-item-title";
      title.textContent = r.address || (r.bbl ? "BBL " + r.bbl : "Property report");
      a.appendChild(title);

      // The original question, shown as muted context when present.
      if (r.query) {
        var q = document.createElement("span");
        q.className = "reports-item-query";
        q.textContent = "“" + r.query + "”";
        a.appendChild(q);
      }

      var meta = document.createElement("span");
      meta.className = "reports-item-meta";
      var bits = [];
      if (r.bbl) bits.push("BBL " + r.bbl);
      var dateStr = formatDate(r.created_at);
      if (dateStr) bits.push(dateStr);
      meta.textContent = bits.join("  ·  ");
      a.appendChild(meta);

      li.appendChild(a);
      listEl.appendChild(li);
    });
    show(listEl);
  }

  function load() {
    var token = null;
    try { token = localStorage.getItem(TOKEN_KEY); } catch (e) { token = null; }

    if (!token) {
      hide(statusEl);
      show(signedOutEl);
      capture("reports_history_view", { state: "signed_out" });
      return;
    }

    fetch(API_BASE + "/api/reports/mine", {
      method: "GET",
      headers: { "Authorization": "Bearer " + token },
      credentials: "include",
    })
      .then(function (res) {
        if (res.status === 401) {
          // Token missing/expired — treat as signed-out and clear the stale key.
          try { localStorage.removeItem(TOKEN_KEY); } catch (e) { /* no-op */ }
          hide(statusEl);
          show(signedOutEl);
          capture("reports_history_view", { state: "expired" });
          return null;
        }
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        if (!data) return; // handled above (401)
        hide(statusEl);
        var reports = (data && data.reports) || [];
        if (reports.length === 0) {
          show(emptyEl);
          capture("reports_history_view", { state: "empty", count: 0 });
          return;
        }
        renderList(reports);
        show(ctaEl);
        if (metaEl) {
          metaEl.textContent =
            reports.length === 1
              ? "1 saved report. Every full due-diligence report you run is saved here automatically."
              : reports.length + " saved reports. Every full due-diligence report you run is saved here automatically.";
        }
        capture("reports_history_view", { state: "list", count: reports.length });
      })
      .catch(function (err) {
        hide(statusEl);
        if (statusEl) {
          statusEl.hidden = false;
          statusEl.textContent = "";
          var p = document.createElement("p");
          p.textContent = "Couldn’t load your reports right now. Please refresh in a moment.";
          statusEl.appendChild(p);
        }
        capture("reports_history_view", { state: "error" });
        if (typeof console !== "undefined") console.warn("reports load failed", err);
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load);
  } else {
    load();
  }
})();
