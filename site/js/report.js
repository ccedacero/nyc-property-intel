/**
 * NYC Property Intel — shared report viewer (/r/<id>).
 *
 * Reads the report id from the path (/r/<id>, rewritten to /report.html by
 * vercel.json), fetches the persisted report JSON from the Railway API, and
 * renders the stored markdown with the same marked + DOMPurify pipeline the
 * chat uses. Public and auth-free by design — this is the referral surface.
 */
(function () {
  "use strict";

  var API_BASE = "https://nyc-property-intel-production.up.railway.app";

  var titleEl = document.getElementById("report-title");
  var metaEl = document.getElementById("report-meta");
  var statusEl = document.getElementById("report-status");
  var bodyEl = document.getElementById("report-body");
  var ctaEl = document.getElementById("report-cta");

  function renderMarkdown(text) {
    if (typeof marked === "undefined" || typeof DOMPurify === "undefined") {
      var pre = document.createElement("pre");
      pre.textContent = text;
      return pre.outerHTML;
    }
    // Match chat.js: render "~" literally (the model uses it for "approximately").
    if (marked.use) {
      marked.use({ renderer: { del: function (t) { return "~" + t + "~"; } } });
    }
    return DOMPurify.sanitize(marked.parse(text));
  }

  function showError(message) {
    if (titleEl) titleEl.textContent = "Report not found";
    if (statusEl) {
      statusEl.innerHTML =
        '<p>' + message + '</p>' +
        '<p><a href="/chat" class="btn btn-primary">Run a free report &rarr;</a></p>';
    }
  }

  function getReportId() {
    // Path is /r/<id> (rewritten to /report.html). Fall back to ?id= for safety.
    var m = window.location.pathname.match(/\/r\/([A-Za-z0-9_-]{6,32})\/?$/);
    if (m) return m[1];
    var params = new URLSearchParams(window.location.search);
    return params.get("id");
  }

  function formatDate(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso);
      return d.toLocaleDateString("en-US", {
        year: "numeric", month: "long", day: "numeric",
      });
    } catch (e) {
      return "";
    }
  }

  function init() {
    var id = getReportId();
    if (!id) {
      showError("This report link is invalid.");
      return;
    }

    fetch(API_BASE + "/api/report/" + encodeURIComponent(id))
      .then(function (res) {
        if (res.status === 404) throw new Error("not_found");
        if (!res.ok) throw new Error("unavailable");
        return res.json();
      })
      .then(function (data) {
        var heading = data.address
          ? "Due-Diligence Report — " + data.address
          : "NYC Property Due-Diligence Report";
        if (titleEl) titleEl.textContent = heading;
        document.title = heading + " | NYC Property Intel";

        var metaBits = [];
        if (data.bbl) metaBits.push("BBL " + data.bbl);
        if (data.created_at) metaBits.push("Generated " + formatDate(data.created_at));
        if (metaEl) metaEl.textContent = metaBits.join(" · ");

        if (statusEl) statusEl.hidden = true;
        if (bodyEl) bodyEl.innerHTML = renderMarkdown(data.report_md || "");
        if (ctaEl) ctaEl.hidden = false;

        if (typeof posthog !== "undefined") {
          posthog.capture("shared_report_viewed", { bbl: data.bbl || null });
        }
      })
      .catch(function (err) {
        if (err && err.message === "not_found") {
          showError("This report doesn't exist or has expired.");
        } else {
          showError("We couldn't load this report right now. Please try again in a moment.");
        }
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
