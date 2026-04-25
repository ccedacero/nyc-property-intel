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
        token = data.token;
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
        appendSystemMessage("You're activated! You now have 10 queries/day including up to 5 full analysis reports.");
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
            data.message || data.detail || "You've reached your daily query limit. It resets at midnight UTC."
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
            if (toolIndicatorEl) {
              toolIndicatorEl.remove();
              toolIndicatorEl = null;
            }
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
            if (toolIndicatorEl) {
              toolIndicatorEl.remove();
              toolIndicatorEl = null;
            }

          } else if (evt.type === "error") {
            if (toolIndicatorEl) { toolIndicatorEl.remove(); toolIndicatorEl = null; }
            appendErrorMessage(evt.message || "An error occurred. Please try again.");

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
      appendErrorMessage("Connection error. Please check your network and try again.");
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
      <p class="chat-gate-heading">Enjoying NYC Property Intel?</p>
      <p class="chat-gate-sub">
        You've used your 3 free queries. Enter your email to get
        <strong>10 queries/day free for 30 days</strong> — including up to
        5 full due-diligence reports. No credit card required.
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
        if (!res.ok) {
          gateError.textContent = data.error || "Something went wrong. Please try again.";
          submitBtn.disabled = false;
          submitBtn.textContent = "Get free access";
        } else {
          gate.innerHTML = `
            <p class="chat-gate-heading">Check your inbox!</p>
            <p class="chat-gate-sub">
              We sent an activation link to <strong>${escapeHtml(email)}</strong>.
              Click it to unlock 10 queries/day free for 30&nbsp;days.
            </p>
          `;
          if (typeof posthog !== "undefined") posthog.capture("chat_signup", { email });
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
      authLabel.textContent = `Trial active · ${trialQueryCount}/${TRIAL_LIMIT} queries today`;
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
        freeCounter.innerHTML = `<span>${FREE_LIMIT}</span> free queries — no account needed`;
      } else {
        freeCounter.innerHTML = `<span>${remaining}</span> of ${FREE_LIMIT} free queries remaining`;
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
        Ask about any NYC property in plain English. I'll pull violations,
        sales history, liens, permits, ownership records, and more from
        official city databases.
      </p>
    `;
    messagesEl.appendChild(welcome);
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
