# Web Chat — Implementation Plan

## Overview

Add a browser-based chat interface at `nycpropertyintel.com/chat` that lets visitors query
NYC property data without needing Claude Desktop or CLI. The backend calls Claude (Haiku 4.5)
with the existing 18 MCP tools in `tool_use` mode and streams responses via SSE.

---

## Cost Model

- **Model**: Claude Haiku 4.5 (`$1/MTok` input · `$5/MTok` output)
- **Prompt caching**: system prompt + tool schemas cached (~5,600 tokens fixed overhead)
- **Per query cost**: ~$0.01 (simple lookup) to ~$0.029 (`analyze_property`)
- **Worst case**: 100 trial users maxing daily limits = ~$321/month

---

## Trial Limits

| Limit | Value |
|-------|-------|
| Total queries/day | 10 |
| `analyze_property`/day | 5 |
| Free queries (no signup) | 3 |
| Trial duration | 30 days |

---

## Auth Flow

```
ANONYMOUS (0–2 queries)
  Signed cookie: nyprop_sess = HMAC-SHA256 {q:0, t:<ts>}
      │ 3rd query sent
      ▼
GATE_EMAIL
  Inline email form in chat thread
  User submits → Loops form (source=web_chat hidden field)
      │ Loops webhook fires → token provisioned → magic link emailed
      ▼
GATE_TOKEN
  User clicks /activate?t=<uuid>
  POST /api/activate → {token: "nyprop_xxx..."}
  localStorage.setItem("nyprop_token", token)
      │
      ▼
AUTHENTICATED (trial, 30 days)
  Authorization: Bearer nyprop_xxx
  Validated by existing TokenAuth.validate()
      │ expires_at < NOW()
      ▼
EXPIRED → upgrade prompt
```

---

## Architecture

`/api/chat` and `/api/activate` live **inside the existing Starlette app** on Railway alongside
`/mcp` and `/webhook/loops`. Tools execute in-process via `mcp._tool_manager.call_tool()`.

```
BROWSER (nycpropertyintel.com/chat)
  POST /api/chat {messages: [...]}
  Cookie: nyprop_sess=<signed>   ← free-tier counter
  Authorization: Bearer nyprop_xxx  ← after signup
        │
        ▼
RAILWAY — CORSMiddleware
  /api/chat → chat_handler()
    1. Validate Bearer OR read signed cookie
    2. IP rate limit (slowapi: 10/min · 50/hr · 200/day)
    3. Free-tier gate: cookie q >= 3 → SSE {type:"gate"}
    4. Build Anthropic tool list from mcp.list_tools()
    5. Agentic loop → StreamingResponse (SSE)
         Call claude-haiku-4-5 with tools
         Stream text_delta → browser
         On tool_use: execute in-process → feed result back
         On end_turn: yield {type:"done"}

  /api/activate → activate_handler()
    Validate magic link UUID → decrypt token → return to browser
```

---

## New Files

| File | Purpose |
|------|---------|
| `src/nyc_property_intel/chat.py` | `/api/chat` + `/api/activate` handlers, agentic loop, signed cookie |
| `site/chat.html` | Dedicated chat page |
| `site/js/chat.js` | SSE client, auth state machine, email gate UX |
| `site/css/chat.css` | Chat styles (extends existing CSS vars) |
| `site/js/vendor/marked.min.js` | Vendored markdown renderer |
| `site/js/vendor/purify.min.js` | Vendored XSS sanitizer |

## Modified Files

| File | Change |
|------|--------|
| `pyproject.toml` | Add `anthropic`, `slowapi`, `cryptography` |
| `config.py` | Add `anthropic_api_key`, `cookie_secret`, `web_chat_token_key`, chat limits |
| `server.py` | Add CORSMiddleware, mount `/api/chat` + `/api/activate` |
| `loops_webhook.py` | Create magic link for `source=web_chat` signups |
| `scripts/manage_tokens.py` | Add `web_magic_links` migration + `source` column |
| `site/vercel.json` | Add Railway to `connect-src` CSP |
| `site/index.html` | Add "Try It" nav link |

---

## New DB Table

```sql
CREATE TABLE IF NOT EXISTS web_magic_links (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash       TEXT        NOT NULL REFERENCES mcp_tokens(token_hash),
    encrypted_token  TEXT        NOT NULL,   -- Fernet(plaintext nyprop_xxx)
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at       TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '15 minutes',
    used_at          TIMESTAMPTZ
);
```

No anonymous sessions table — the signed cookie handles free-query state without DB writes.

---

## New Environment Variables

```bash
ANTHROPIC_API_KEY=sk-ant-...          # Claude API (Haiku 4.5)
COOKIE_SECRET=<32 random bytes>       # HMAC signing for session cookie
WEB_CHAT_TOKEN_KEY=<Fernet key>       # Encrypt token in magic link rows
```

---

## Security

| Threat | Mitigation |
|--------|-----------|
| API key cost abuse | `slowapi`: 10/min · 50/hr · 200/day per IP |
| Cookie forgery | HMAC-SHA256 signed cookie (`COOKIE_SECRET`) |
| Prompt injection | `system=` param only, max 2,000 char input |
| Tool loop abuse | Max 5 tool calls/turn, 60s stream timeout |
| Token delivery | Magic link + Fernet-encrypted token (single-use, 15min TTL) |
| XSS in markdown | `marked.js` + `DOMPurify` (vendored, no CDN) |
| CORS | Exact origin `https://nycpropertyintel.com`, `allow_credentials=True` |
| API key exposure | Railway env var only, never in any response |

---

## Build Phases

**Phase 1 — Foundations** (config, deps, DB migration, CORS, CSP)
**Phase 2 — Token activation** (`/api/activate`, magic link in loops_webhook)
**Phase 3 — Chat endpoint** (agentic loop, SSE streaming, session gate)
**Phase 4 — Frontend** (chat.html, chat.js, chat.css, vendor libs)
**Phase 5 — Deploy** (Railway env vars, end-to-end test, nav link)
