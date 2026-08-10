/**
 * NYC Property Intel — "Your Watched Buildings" management page (/watches).
 *
 * Mirrors reports.js: reads the trial token from localStorage, fetches the
 * caller's active watches (joined server-side on the token's email), and
 * renders them with a per-row "Stop watching" action that reuses the
 * unsubscribe endpoint (each watch row id doubles as its removal token).
 *
 * Address strings are user/city-record-influenced, so everything renders via
 * textContent — never innerHTML.
 */
(function () {
  "use strict";

  var API_BASE = "https://nyc-property-intel-production.up.railway.app";
  var TOKEN_KEY = "nyc_pi_token";

  var statusEl = document.getElementById("watches-status");
  var signedOutEl = document.getElementById("watches-signedout");
  var listEl = document.getElementById("watches-list");
  var emptyEl = document.getElementById("watches-empty");
  var ctaEl = document.getElementById("watches-cta");
  var metaEl = document.getElementById("watches-meta");

  function show(el) { if (el) el.hidden = false; }
  function hide(el) { if (el) el.hidden = true; }

  function capture(event, props) {
    if (typeof posthog !== "undefined") {
      try { posthog.capture(event, props || {}); } catch (e) { /* no-op */ }
    }
  }

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

  function stopWatching(watch, li, btn) {
    btn.disabled = true;
    btn.textContent = "Stopping…";
    fetch(API_BASE + "/api/watch/unsubscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: watch.id }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        li.remove();
        capture("watch_removed_from_page", { bbl: watch.bbl || null });
        if (listEl && listEl.children.length === 0) {
          hide(listEl); hide(ctaEl); show(emptyEl);
        }
      })
      .catch(function () {
        btn.disabled = false;
        btn.textContent = "Couldn't stop — retry";
      });
  }

  function renderList(watches) {
    listEl.textContent = "";
    watches.forEach(function (w) {
      var li = document.createElement("li");
      li.className = "reports-item";

      var row = document.createElement("div");
      row.className = "reports-item-link";

      var title = document.createElement("span");
      title.className = "reports-item-title";
      title.textContent = w.address || (w.bbl ? "BBL " + w.bbl : "Watched building");
      row.appendChild(title);

      var meta = document.createElement("span");
      meta.className = "reports-item-meta";
      var bits = [];
      if (w.bbl) bits.push("BBL " + w.bbl);
      var since = formatDate(w.created_at);
      if (since) bits.push("watching since " + since);
      bits.push(w.last_notified_at ? "last alert " + formatDate(w.last_notified_at) : "no alerts yet");
      if (!w.confirmed) bits.push("pending email confirmation");
      meta.textContent = bits.join("  ·  ");
      row.appendChild(meta);

      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-outline btn-sm";
      btn.textContent = "Stop watching";
      btn.setAttribute("aria-label", "Stop watching " + (w.address || w.bbl || "this building"));
      btn.addEventListener("click", function () { stopWatching(w, li, btn); });
      row.appendChild(btn);

      li.appendChild(row);
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
      capture("watches_page_view", { state: "signed_out" });
      return;
    }

    fetch(API_BASE + "/api/watch/mine", {
      method: "GET",
      headers: { "Authorization": "Bearer " + token },
      credentials: "include",
    })
      .then(function (res) {
        if (res.status === 401) {
          try { localStorage.removeItem(TOKEN_KEY); } catch (e) { /* no-op */ }
          hide(statusEl);
          show(signedOutEl);
          capture("watches_page_view", { state: "expired" });
          return null;
        }
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        if (!data) return; // handled 401 above
        hide(statusEl);
        var watches = (data && data.watches) || [];
        if (watches.length === 0) {
          show(emptyEl);
          capture("watches_page_view", { state: "empty", count: 0 });
          return;
        }
        renderList(watches);
        show(ctaEl);
        if (metaEl) {
          metaEl.textContent =
            watches.length === 1
              ? "1 watched building. You'll get one email if it picks up a new violation or litigation — at most once a week."
              : watches.length + " watched buildings. You'll get one email when any of them picks up a new violation or litigation — at most once per building per week.";
        }
        capture("watches_page_view", { state: "list", count: watches.length });
      })
      .catch(function (err) {
        hide(statusEl);
        if (statusEl) {
          statusEl.hidden = false;
          statusEl.textContent = "";
          var p = document.createElement("p");
          p.textContent = "Couldn’t load your watched buildings right now. Please refresh in a moment.";
          statusEl.appendChild(p);
        }
        capture("watches_page_view", { state: "error" });
        if (typeof console !== "undefined") console.warn("watches load failed", err);
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load);
  } else {
    load();
  }
})();
