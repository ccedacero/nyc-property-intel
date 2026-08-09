/**
 * NYC Property Intel — Web Chat Client
 *
 * Auth state machine:
 *   anon      → up to FREE_LIMIT free queries (signed cookie tracks count)
 *   gate      → FREE_LIMIT reached; email gate shown inline
 *   trial     → token in localStorage, trial limits apply
 *   activated → same as trial but just activated from magic link
 */

(function () {
  "use strict";

  /* ── Config ──────────────────────────────────────────────────────── */

  const API_BASE = "https://nyc-property-intel-production.up.railway.app";
  const FREE_LIMIT = 3;
  const TRIAL_LIMIT = 10;
  const TOKEN_KEY = "nyc_pi_token";
  const EMAIL_KEY = "nyc_pi_email";
  const QUERY_COUNT_KEY = "nyc_pi_qcount";
  const TRIAL_COUNT_KEY = "nyc_pi_trial_count";
  const TRIAL_DATE_KEY  = "nyc_pi_trial_date";

  /* ── State ───────────────────────────────────────────────────────── */

  let authState = "anon"; // anon | gate | trial
  let token = null;
  let queryCount = 0;
  let trialQueryCount = 0;
  let isStreaming = false;
  /** @type {Array<{role: string, content: string}>} */
  let messages = [];

  /* ── DOM refs ─────────────────────────────────────────────────────── */

  const messagesEl = document.getElementById("chat-messages");
  const welcomeEl = document.getElementById("chat-welcome");
  const form = document.getElementById("chat-form");
  const textarea = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send-btn");
  const freeCounter = document.getElementById("free-counter");
  const authDot = document.getElementById("auth-dot");
  const authLabel = document.getElementById("auth-label");
  const newChatBtn = document.getElementById("chat-new-btn");
  const sidebar = document.getElementById("chat-sidebar");

  /* ── Markdown renderer ────────────────────────────────────────────── */

  function renderMarkdown(text) {
    if (typeof marked === "undefined" || typeof DOMPurify === "undefined") {
      return escapeHtml(text).replace(/\n/g, "<br>");
    }
    return DOMPurify.sanitize(marked.parse(text));
  }

  function escapeHtml(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* Disable Markdown strikethrough. The model uses "~" as shorthand for
     "approximately" (e.g. ~$1.9M); GFM parses a pair of tildes as a
     strikethrough span and would silently cross out part of a report.
     Render the tildes literally instead. */
  if (typeof marked !== "undefined") {
    marked.use({
      renderer: {
        del(text) {
          return "~" + text + "~";
        },
      },
    });
  }

  /* ── Init ─────────────────────────────────────────────────────────── */

  function init() {
    // Check for magic-link activation token in URL
    const params = new URLSearchParams(window.location.search);
    const magicId = params.get("t");
    if (magicId) {
      activateMagicLink(magicId);
      // Clean URL without reload
      window.history.replaceState({}, "", window.location.pathname);
    }

    // Restore stored token
    const stored = localStorage.getItem(TOKEN_KEY);
    if (stored) {
      token = stored;
      authState = "trial";
    }

    // Restore anon query count from localStorage (server sets a signed cookie
    // as the authoritative counter; we mirror it here for UI only)
    const stored_count = parseInt(localStorage.getItem(QUERY_COUNT_KEY) || "0", 10);
    queryCount = isNaN(stored_count) ? 0 : stored_count;

    // Restore trial query count — reset if date has rolled over (UTC)
    const todayUTC = new Date().toISOString().slice(0, 10);
    if (localStorage.getItem(TRIAL_DATE_KEY) !== todayUTC) {
      localStorage.setItem(TRIAL_DATE_KEY, todayUTC);
      localStorage.setItem(TRIAL_COUNT_KEY, "0");
    }
    const stored_trial = parseInt(localStorage.getItem(TRIAL_COUNT_KEY) || "0", 10);
    trialQueryCount = isNaN(stored_trial) ? 0 : stored_trial;

    updateAuthUI();
    updateCounter();

    // Event listeners
    form.addEventListener("submit", onSubmit);
    textarea.addEventListener("input", onTextareaInput);
    textarea.addEventListener("keydown", onTextareaKeydown);
    newChatBtn.addEventListener("click", onNewChat);

    // Suggestion pills
    document.querySelectorAll(".chat-suggestion-pill").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (isStreaming) return;
        textarea.value = btn.textContent.trim();
        textarea.dispatchEvent(new Event("input"));
        textarea.focus();
      });
    });

    // Mobile sidebar toggle (hamburger menu opens sidebar)
    const navToggle = document.querySelector(".nav-toggle");
    if (navToggle) {
      navToggle.addEventListener("click", () => {
        sidebar.classList.toggle("open");
      });
    }

    // Prefill from ?q= (lookup-page handoff). Never auto-submit — the user
    // confirms with Send, and the anon counter only increments on submit.
    const prefill = params.get("q");
    if (prefill) {
      textarea.value = prefill.slice(0, 500);
      textarea.dispatchEvent(new Event("input"));
      textarea.focus();
      window.history.replaceState({}, "", window.location.pathname);
      if (typeof posthog !== "undefined") posthog.capture("chat_prefill_arrival");
    }
  }

  /* ── Apply a freshly-issued trial token (magic-link OR instant signup) ── */

  function applyIssuedToken(newToken) {
    token = newToken;
    authState = "trial";
    localStorage.setItem(TOKEN_KEY, token);
    // Reset anon counter — they now have a proper token
    queryCount = 0;
    localStorage.setItem(QUERY_COUNT_KEY, "0");
    // Reset trial counter for the new day
    trialQueryCount = 0;
    const todayUTC = new Date().toISOString().slice(0, 10);
    localStorage.setItem(TRIAL_DATE_KEY, todayUTC);
    localStorage.setItem(TRIAL_COUNT_KEY, "0");
    updateAuthUI();
    updateCounter();
    appendSystemMessage("You're in. 10 queries per day, including up to 5 full due-diligence reports. Start with an address below.");
  }

  /* ── Magic link activation ────────────────────────────────────────── */

  async function activateMagicLink(id) {
    try {
      const res = await fetch(`${API_BASE}/api/activate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ magic_token: id }),
        credentials: "include",
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        appendSystemMessage(
          data.error || "Activation link expired or already used. Please request a new one."
        );
        return;
      }
      const data = await res.json();
      if (data.token) {
        applyIssuedToken(data.token);
        if (typeof posthog !== "undefined") posthog.capture("chat_activated");
      }
    } catch {
      appendSystemMessage("Could not activate your account. Please try again.");
    }
  }

  /* ── Submit ───────────────────────────────────────────────────────── */

  async function onSubmit(e) {
    e.preventDefault();
    if (isStreaming) return;

    const text = textarea.value.trim();
    if (!text) return;

    // Hide welcome state
    if (welcomeEl) welcomeEl.remove();

    // Gate check — block if limit reached or gate already showing
    if (authState === "gate") {
      showEmailGate();
      return;
    }
    if (authState === "anon" && queryCount >= FREE_LIMIT) {
      authState = "gate";
      updateSendBtn();
      showEmailGate();
      return;
    }

    textarea.value = "";
    textarea.style.height = "";
    sendBtn.disabled = true;

    appendUserMessage(text);
    messages.push({ role: "user", content: text });

    // Anon: increment counter optimistically
    if (authState === "anon") {
      queryCount++;
      localStorage.setItem(QUERY_COUNT_KEY, String(queryCount));
      updateCounter();
    }

    await streamAssistant(text);
  }

  /* ── Stream ───────────────────────────────────────────────────────── */

  async function streamAssistant() {
    isStreaming = true;

    const thinkingEl = appendThinking();
    let assistantEl = null;
    let assistantText = "";
    let toolIndicatorEl = null;
    // Post-message extras render in a fixed order: report permalink → watch
    // box → follow-up chips. Track the tail so chips never jump the queue.
    let extrasTail = null;
    let savedReportThisTurn = false;
    let resolvedProperty = null;

    const headers = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;

    try {
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers,
        credentials: "include",
        body: JSON.stringify({ messages }),
      });

      // Remove thinking dots once we have a response
      thinkingEl.remove();

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const msg = data.error || `Request failed (${res.status})`;

        if (res.status === 402) {
          // Free limit reached (backend enforcement)
          authState = "gate";
          updateSendBtn();
          updateAuthUI();
          updateCounter();
          showEmailGate();
        } else if (res.status === 401 || res.status === 403) {
          // Token rejected — clear it and drop back to anon/gate
          token = null;
          localStorage.removeItem(TOKEN_KEY);
          authState = queryCount >= FREE_LIMIT ? "gate" : "anon";
          updateSendBtn();
          updateAuthUI();
          showEmailGate();
        } else if (res.status === 429) {
          appendErrorMessage(
            data.message || data.detail || "You've reached your daily limit — it resets tonight at 8 PM ET (midnight UTC)."
          );
        } else {
          appendErrorMessage(msg);
        }
        return;
      }

      // Read SSE stream
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop(); // keep incomplete line

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          let evt;
          try { evt = JSON.parse(line.slice(6)); } catch { continue; }

          if (evt.type === "text_delta") {
            if (toolIndicatorEl) { toolIndicatorEl.remove(); toolIndicatorEl = null; }
            if (!assistantEl) {
              assistantEl = appendAssistantMessage("");
              assistantEl.classList.add("streaming");
            }
            assistantText += evt.text;
            assistantEl.innerHTML = renderMarkdown(assistantText);

          } else if (evt.type === "tool_start") {
            if (!toolIndicatorEl) {
              toolIndicatorEl = appendToolIndicator(evt.name || "Querying city data…");
            }

          } else if (evt.type === "tool_done") {
            // Keep indicator visible until first text_delta to avoid blank gap

          } else if (evt.type === "property_resolved") {
            // A BBL was resolved this turn (any lookup, not just a full
            // report) — remember it and offer the watch box after the answer.
            resolvedProperty = evt;

          } else if (evt.type === "report_saved") {
            // A full analysis was persisted as a shareable permalink (/r/<id>).
            if (evt.url) {
              savedReportThisTurn = true;
              const permalinkBox = appendReportPermalink(assistantEl, evt.url);
              const watchBox = appendWatchCTA(permalinkBox, evt);
              extrasTail = watchBox || permalinkBox || extrasTail;
            }

          } else if (evt.type === "error") {
            if (toolIndicatorEl) { toolIndicatorEl.remove(); toolIndicatorEl = null; }
            appendErrorMessage(evt.message || "Something went wrong pulling that data. Try rephrasing your query or try again in a moment.");

          } else if (evt.type === "done") {
            if (assistantEl) assistantEl.classList.remove("streaming");
            if (toolIndicatorEl) { toolIndicatorEl.remove(); toolIndicatorEl = null; }
          }
        }
      }

      // Save assistant reply to history
      if (assistantText) {
        if (assistantEl) assistantEl.classList.remove("streaming");
        messages.push({ role: "assistant", content: assistantText });
        // Plain lookup (no report permalink) that still resolved a building →
        // offer the watch box here. The per-BBL dedupe keeps this quiet when
        // report_saved already rendered one.
        if (!savedReportThisTurn && resolvedProperty) {
          const watchBox = appendWatchCTA(extrasTail || assistantEl, resolvedProperty);
          if (watchBox) extrasTail = watchBox;
        }
        // Show follow-up chips after each assistant response — progressive
        // disclosure of the expensive full-DD path. Real users click; tire-
        // kickers walk away. Click = explicit signal, $0.32 well-spent.
        // Chained after the permalink/watch boxes, and without the redundant
        // "Full DD report" chip when a full report just ran.
        appendFollowupChips(extrasTail || assistantEl, savedReportThisTurn);
      }

      // If anon and now at limit, prompt for email
      if (authState === "anon" && queryCount >= FREE_LIMIT) {
        authState = "gate";
        showEmailGate();
      }

      // Track trial query count client-side (resets daily at midnight UTC)
      if (authState === "trial") {
        trialQueryCount++;
        localStorage.setItem(TRIAL_COUNT_KEY, String(trialQueryCount));
      }

    } catch (err) {
      thinkingEl.remove();
      if (toolIndicatorEl) { toolIndicatorEl.remove(); }
      if (assistantEl) assistantEl.classList.remove("streaming");
      appendErrorMessage("Connection error — please try again. If this keeps happening, the server may be temporarily unavailable.");
      console.error(err);
    } finally {
      isStreaming = false;
      updateSendBtn();
      updateCounter();
      updateAuthUI();
    }
  }

  /* ── Email gate ───────────────────────────────────────────────────── */

  function showEmailGate() {
    const gate = document.createElement("div");
    gate.className = "chat-gate";
    gate.setAttribute("role", "region");
    gate.setAttribute("aria-label", "Email signup gate");
    gate.innerHTML = `
      <p class="chat-gate-heading">Continue with 10 free queries/day</p>
      <p class="chat-gate-sub">
        Get <strong>10 queries/day free for 30 days</strong> — including up to
        5 full due-diligence reports. No credit card. Drop your email and keep going — no link to click.
      </p>
      <form class="chat-gate-form" id="gate-form" novalidate>
        <input
          type="email"
          class="chat-gate-input"
          id="gate-email"
          placeholder="your@email.com"
          autocomplete="email"
          required
          aria-label="Email address"
        >
        <button type="submit" class="gate-submit-btn" id="gate-submit" disabled aria-disabled="true">Get free access</button>
      </form>
      <p class="chat-gate-error" id="gate-error" role="alert" aria-live="polite"></p>
    `;
    messagesEl.appendChild(gate);
    scrollToBottom();

    const gateForm = gate.querySelector("#gate-form");
    const gateEmail = gate.querySelector("#gate-email");
    const gateError = gate.querySelector("#gate-error");
    const gateSubmit = gate.querySelector("#gate-submit");

    // Enable button only when a plausible email is typed
    gateEmail.addEventListener("input", () => {
      const valid = validateEmail(gateEmail.value.trim());
      gateSubmit.disabled = !valid;
      gateSubmit.setAttribute("aria-disabled", String(!valid));
    });

    gateForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const email = gateEmail.value.trim();
      if (!validateEmail(email)) {
        gateError.textContent = "Please enter a valid email address.";
        return;
      }
      gateError.textContent = "";
      const submitBtn = gateSubmit;
      submitBtn.disabled = true;
      submitBtn.textContent = "Sending…";

      try {
        const res = await fetch(`${API_BASE}/api/chat/signup`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email }),
          credentials: "include",
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          // Remember the email so later asks (e.g. the watch box) prefill
          // instead of asking again.
          try { localStorage.setItem(EMAIL_KEY, email); } catch { /* no-op */ }
        }
        if (!res.ok) {
          gateError.textContent = data.error || "Something went wrong. Please try again.";
          submitBtn.disabled = false;
          submitBtn.textContent = "Get free access";
        } else if (data.token) {
          // Brand-new email: token issued instantly — no round-trip. Drop the
          // gate and let them keep working right here.
          gate.remove();
          applyIssuedToken(data.token);
          if (typeof posthog !== "undefined") posthog.capture("chat_signup", { email, instant: true });
        } else {
          // Returning email: token is gated behind the emailed link (prevents
          // email-based token hijack). Ask them to click it.
          gate.innerHTML = `
            <p class="chat-gate-heading">Check your inbox.</p>
            <p class="chat-gate-sub">
              Looks like you've signed up before — we sent a fresh activation link to
              <strong>${escapeHtml(email)}</strong>. Click it to continue with 10 queries/day.
            </p>
          `;
          if (typeof posthog !== "undefined") posthog.capture("chat_signup", { email, instant: false });
        }
      } catch {
        gateError.textContent = "Connection error. Please try again.";
        submitBtn.disabled = false;
        submitBtn.textContent = "Get free access";
      }
    });

    // Focus email input after a short delay
    setTimeout(() => gateEmail.focus(), 100);
  }

  /* ── DOM helpers ─────────────────────────────────────────────────── */

  function appendUserMessage(text) {
    const el = document.createElement("div");
    el.className = "chat-message chat-message-user";
    el.textContent = text;
    el.setAttribute("aria-label", "You said: " + text);
    messagesEl.appendChild(el);
    scrollToBottom();
    return el;
  }

  function appendAssistantMessage(text) {
    const el = document.createElement("div");
    el.className = "chat-message chat-message-assistant";
    el.innerHTML = text ? renderMarkdown(text) : "";
    messagesEl.appendChild(el);
    scrollToBottom();
    return el;
  }

  // Render a shareable permalink box after a full analysis is persisted.
  // The link (/r/<id>) renders cold for anyone — no account — which is the
  // free referral loop: forward the report to a partner, lender, or attorney.
  function appendReportPermalink(afterEl, relUrl) {
    const fullUrl = window.location.origin + relUrl;
    const box = document.createElement("div");
    box.className = "chat-report-permalink";
    // For signed-in users the report is also saved to their private history
    // (/reports) — the retention surface. Anonymous users only get the
    // anonymous shareable link, so we don't promise them a history.
    const savedLine = (authState === "trial")
      ? `<a class="chat-report-permalink-saved" href="/reports">✓ Saved to <strong>Your Reports</strong> &rarr;</a>`
      : "";
    box.innerHTML = `
      <span class="chat-report-permalink-label">🔗 Shareable report link</span>
      <div class="chat-report-permalink-row">
        <input type="text" class="chat-report-permalink-input" readonly value="${escapeHtml(fullUrl)}" aria-label="Shareable report link">
        <button type="button" class="chat-report-permalink-copy">Copy</button>
      </div>
      ${savedLine}
    `;
    if (afterEl && afterEl.parentNode) {
      afterEl.parentNode.insertBefore(box, afterEl.nextSibling);
    } else {
      messagesEl.appendChild(box);
    }
    const input = box.querySelector(".chat-report-permalink-input");
    const btn = box.querySelector(".chat-report-permalink-copy");
    btn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(fullUrl);
      } catch {
        input.select();
        document.execCommand("copy");
      }
      btn.textContent = "Copied ✓";
      setTimeout(() => { btn.textContent = "Copy"; }, 2000);
      if (typeof posthog !== "undefined") posthog.capture("report_permalink_copied");
    });
    return box;
  }

  // Painted-door WTP probe (no billing) — same signal report.js collects on
  // /r/<id>, ported here so the higher-volume chat surface samples it too.
  function appendChatProProbe(box, email, bbl) {
    const probe = document.createElement("div");
    probe.className = "chat-watch-probe";
    probe.innerHTML =
      "<p>Watching more than one building? <strong>Pro monitoring</strong> — " +
      "unlimited buildings + an alert on every change (no weekly cap), " +
      "<strong>$19/mo</strong>.</p>" +
      '<button type="button" class="btn btn-sm btn-accent">Notify me at launch</button>';
    box.appendChild(probe);
    probe.querySelector("button").addEventListener("click", () => {
      if (typeof posthog !== "undefined") {
        posthog.capture("pro_monitoring_interest", {
          price_shown: 19, bbl: bbl || null, email: email || null, source: "chat",
        });
      }
      probe.innerHTML = "<p class=\"chat-watch-msg\">✓ We'll email you when Pro monitoring launches.</p>";
    });
  }

  // "Watch this building" (feature 1.9), surfaced where buildings are actually
  // looked up. Same /api/watch endpoint the /r/<id> permalink page uses.
  // Sync path (bbl known) returns the box so callers can chain insertion
  // order; the async fallback (older backend: report_saved without bbl)
  // returns null and inserts when the fetch resolves.
  function appendWatchCTA(afterEl, evt) {
    const render = (bbl, address) => {
      if (!bbl) return null;
      // Insertion anchor gone (e.g. "New chat" while the fallback fetch was
      // in flight) → don't drop a stale box into a fresh conversation.
      if (afterEl && !afterEl.isConnected) return null;
      // One box per building per conversation — but if the earlier box was
      // abandoned unsubmitted, move the offer down to where the user is now.
      const existing = messagesEl.querySelector(`.chat-watch-box[data-bbl="${bbl}"]`);
      if (existing) {
        if (existing.querySelector("form")) existing.remove();
        else return null; // already subscribed — don't re-ask
      }

      const box = document.createElement("div");
      box.className = "chat-watch-box";
      box.dataset.bbl = bbl;
      box.innerHTML = `
        <p class="chat-watch-heading">🔔 Watch this building — free</p>
        <p class="chat-watch-sub">Get an email if ${address ? escapeHtml(address) : "this building"} picks up a new violation or litigation. Only when something changes — at most one email a week, no spam.</p>
        <form class="chat-watch-form" novalidate>
          <input type="email" class="chat-watch-input" placeholder="you@email.com" autocomplete="email" required aria-label="Email address for building alerts">
          <button type="submit" class="chat-watch-btn">Watch this building</button>
        </form>
        <p class="chat-watch-msg" role="status" aria-live="polite"></p>
      `;
      if (afterEl && afterEl.parentNode) {
        afterEl.parentNode.insertBefore(box, afterEl.nextSibling);
      } else {
        messagesEl.appendChild(box);
      }

      const watchForm = box.querySelector(".chat-watch-form");
      const emailInput = box.querySelector(".chat-watch-input");
      const msgEl = box.querySelector(".chat-watch-msg");

      // Prefill from the email captured at the signup gate (or a previous
      // watch) — a prefilled field converts; a second blank ask doesn't.
      let knownEmail = null;
      try { knownEmail = localStorage.getItem(EMAIL_KEY); } catch { /* no-op */ }
      if (knownEmail && validateEmail(knownEmail)) emailInput.value = knownEmail;

      watchForm.addEventListener("submit", (e) => {
        e.preventDefault();
        const email = (emailInput.value || "").trim();
        if (!validateEmail(email)) {
          msgEl.textContent = "Please enter a valid email address.";
          return;
        }
        const submitBtn = watchForm.querySelector("button");
        submitBtn.disabled = true;
        msgEl.textContent = "Saving…";
        fetch(`${API_BASE}/api/watch`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, bbl, address: address || null }),
        })
          .then((res) => res.json().catch(() => ({})).then((d) => ({ ok: res.ok, d })))
          .then(({ ok, d }) => {
            if (ok) {
              try { localStorage.setItem(EMAIL_KEY, email); } catch { /* no-op */ }
              const msg = d.confirm_required
                ? "✓ Almost there — check your inbox and click the confirmation link to start getting alerts."
                : "✓ You're watching this building. We'll email you if a new violation or litigation shows up.";
              watchForm.remove();
              msgEl.textContent = msg;
              if (typeof posthog !== "undefined") {
                posthog.capture("building_watch_subscribed", {
                  bbl, confirm_required: !!d.confirm_required, source: "chat",
                });
              }
              appendChatProProbe(box, email, bbl);
            } else {
              const map = {
                invalid_email: "Please enter a valid email address.",
                disposable_email: "Please use a non-disposable email address.",
                watch_limit: "You've reached the limit of watched buildings for this email.",
                rate_limited: "Too many requests — please try again in a little while.",
              };
              msgEl.textContent = map[d.error] || "Couldn't save that right now. Please try again.";
              submitBtn.disabled = false;
            }
          })
          .catch(() => {
            msgEl.textContent = "Connection error. Please try again.";
            submitBtn.disabled = false;
          });
      });
      scrollToBottom();
      return box;
    };

    if (evt.bbl) {
      return render(evt.bbl, evt.address);
    }
    if (evt.id) {
      fetch(`${API_BASE}/api/report/${encodeURIComponent(evt.id)}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => { if (d && d.bbl) render(d.bbl, d.address); })
        .catch(() => {});
    }
    return null;
  }

  // Render 3 follow-up suggestion chips after each assistant response.
  // Click = explicit user intent to dig deeper → fires a new query that
  // re-uses conversation history so Claude knows which property to act on.
  function appendFollowupChips(afterEl, reportJustRan) {
    const chips = document.createElement("div");
    chips.className = "chat-followup-chips";
    chips.setAttribute("role", "group");
    chips.setAttribute("aria-label", "Follow-up suggestions");
    // No "Full DD report" chip right after a full report ran — it's redundant
    // and burns another analyze call from the daily cap.
    const fullDDChip = reportJustRan
      ? ""
      : `<button class="chat-followup-chip chat-followup-primary" type="button" data-query="Generate the full due diligence report on the property we just discussed">📋 Full DD report</button>`;
    chips.innerHTML = `
      ${fullDDChip}
      <button class="chat-followup-chip" type="button" data-query="Check any open violations on the property we just discussed">⚠️ Check violations</button>
      <button class="chat-followup-chip" type="button" data-query="Show comparable sales nearby for the property we just discussed">📈 Comparable sales</button>
    `;
    if (afterEl && afterEl.parentNode) {
      afterEl.parentNode.insertBefore(chips, afterEl.nextSibling);
    } else {
      messagesEl.appendChild(chips);
    }
    chips.querySelectorAll(".chat-followup-chip").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (isStreaming) return;
        textarea.value = btn.dataset.query;
        textarea.dispatchEvent(new Event("input"));
        // Auto-submit — explicit click = explicit intent, no need to make
        // them hit Send too.
        if (form.requestSubmit) form.requestSubmit();
        else form.submit();
      });
    });
    scrollToBottom();
  }

  function appendThinking() {
    const el = document.createElement("div");
    el.className = "chat-thinking";
    el.setAttribute("aria-label", "Thinking…");
    el.setAttribute("role", "status");
    el.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span>';
    messagesEl.appendChild(el);
    scrollToBottom();
    return el;
  }

  function appendToolIndicator(toolName) {
    const el = document.createElement("div");
    el.className = "chat-tool-indicator";
    el.setAttribute("role", "status");
    const label = friendlyToolName(toolName);
    el.innerHTML = `<span class="tool-spinner" aria-hidden="true"></span><span>${escapeHtml(label)}</span>`;
    messagesEl.appendChild(el);
    scrollToBottom();
    return el;
  }

  function appendSystemMessage(text) {
    const el = document.createElement("div");
    el.className = "chat-message chat-message-system";
    el.textContent = text;
    messagesEl.appendChild(el);
    scrollToBottom();
    return el;
  }

  function appendErrorMessage(text) {
    const el = document.createElement("div");
    el.className = "chat-message chat-message-error";
    el.textContent = text;
    el.setAttribute("role", "alert");
    messagesEl.appendChild(el);
    scrollToBottom();
    return el;
  }

  function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  /* ── UI state helpers ─────────────────────────────────────────────── */

  function updateAuthUI() {
    if (authState === "trial") {
      authDot.className = "auth-dot active";
      const remaining = Math.max(0, TRIAL_LIMIT - trialQueryCount);
      authLabel.textContent = `Free plan · ${trialQueryCount}/${TRIAL_LIMIT} queries today`;
      authLabel.title = `${remaining} quer${remaining === 1 ? "y" : "ies"} remaining today`;
    } else if (authState === "gate") {
      authDot.className = "auth-dot expired";
      authLabel.textContent = "Free limit reached";
    } else {
      authDot.className = "auth-dot anon";
      const remaining = Math.max(0, FREE_LIMIT - queryCount);
      authLabel.textContent = `${remaining} free quer${remaining === 1 ? "y" : "ies"} remaining`;
    }
  }

  function updateCounter() {
    if (authState === "trial") {
      const remaining = Math.max(0, TRIAL_LIMIT - trialQueryCount);
      if (remaining <= 3 && remaining > 0) {
        freeCounter.innerHTML = `<span>${remaining}</span> quer${remaining === 1 ? "y" : "ies"} left today`;
      } else if (remaining === 0) {
        freeCounter.innerHTML = `Daily limit reached — resets at midnight UTC`;
      } else {
        freeCounter.innerHTML = `<span>${remaining}</span> of ${TRIAL_LIMIT} queries left today`;
      }
    } else if (authState === "gate") {
      freeCounter.innerHTML = "Free limit reached — sign up for 10/day free";
    } else {
      const used = queryCount;
      const remaining = Math.max(0, FREE_LIMIT - used);
      if (used === 0) {
        freeCounter.innerHTML = `<span>${FREE_LIMIT}</span> free queries — no signup required`;
      } else if (remaining === 1) {
        freeCounter.innerHTML = `<span>${remaining}</span> query left — sign up for 10/day free`;
      } else {
        freeCounter.innerHTML = `<span>${remaining}</span> of ${FREE_LIMIT} free queries left — sign up for 10/day free`;
      }
    }
  }

  function updateSendBtn() {
    sendBtn.disabled = isStreaming || authState === "gate" || textarea.value.trim().length === 0;
  }

  /* ── Textarea auto-grow ───────────────────────────────────────────── */

  function onTextareaInput() {
    textarea.style.height = "auto";
    textarea.style.height = Math.min(textarea.scrollHeight, 180) + "px";
    updateSendBtn();
  }

  function onTextareaKeydown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!sendBtn.disabled) form.requestSubmit();
    }
  }

  /* ── New chat ─────────────────────────────────────────────────────── */

  function onNewChat() {
    if (isStreaming) return;
    messages = [];
    messagesEl.innerHTML = "";
    const welcome = document.createElement("div");
    welcome.className = "chat-welcome";
    welcome.id = "chat-welcome";
    welcome.innerHTML = `
      <h1 class="chat-welcome-title">NYC Property Intel</h1>
      <p class="chat-welcome-sub">
        Type an address or BBL to get started. I'll return violations,
        sales history, liens, permits, ownership records, and more from
        20+ official NYC city databases.
      </p>
      <div class="welcome-pills">
        <button class="chat-suggestion-pill" type="button">Look up 350 5th Ave, Manhattan</button>
        <button class="chat-suggestion-pill" type="button">Full due diligence on 250 West St, Manhattan</button>
        <button class="chat-suggestion-pill" type="button">Any red flags on 123 Atlantic Ave, Brooklyn?</button>
      </div>
    `;
    messagesEl.appendChild(welcome);
    // Wire up the inline welcome pills
    welcome.querySelectorAll(".chat-suggestion-pill").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (isStreaming) return;
        textarea.value = btn.textContent.trim();
        textarea.dispatchEvent(new Event("input"));
        textarea.focus();
      });
    });
    textarea.value = "";
    textarea.style.height = "";
    updateSendBtn();
    updateCounter();
    // Re-show the email gate if the user has already hit the limit
    if (authState === "gate") {
      showEmailGate();
    }
    textarea.focus();
    sidebar.classList.remove("open");
  }

  /* ── Utilities ────────────────────────────────────────────────────── */

  function validateEmail(email) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  }

  function friendlyToolName(name) {
    const map = {
      lookup_property: "Looking up property…",
      get_property_issues: "Checking violations…",
      get_property_history: "Pulling sales history…",
      get_hpd_complaints: "Checking HPD complaints…",
      get_hpd_litigations: "Checking HPD litigations…",
      get_hpd_registration: "Checking HPD registration…",
      get_building_permits: "Checking permits…",
      get_liens_and_encumbrances: "Checking liens…",
      get_tax_info: "Pulling tax info…",
      get_rent_stabilization: "Checking rent stabilization…",
      search_comps: "Finding comparable sales…",
      search_neighborhood_stats: "Pulling neighborhood stats…",
      get_fdny_fire_incidents: "Checking FDNY incidents…",
      get_311_complaints: "Checking 311 complaints…",
      get_evictions: "Checking eviction records…",
      get_dob_complaints: "Checking DOB complaints…",
      get_nypd_crime: "Checking crime data…",
      analyze_property: "Running full analysis…",
    };
    return map[name] || `Querying ${name.replace(/_/g, " ")}…`;
  }

  /* ── Boot ─────────────────────────────────────────────────────────── */

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
