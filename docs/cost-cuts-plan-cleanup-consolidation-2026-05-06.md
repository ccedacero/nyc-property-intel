# Cost-Cut #3 — Fold cleanup-cron into the weekly tier-2 sync (2026-05-06)

Consolidate `nyc-property-intel-cron-cleanup` into `nyc-property-intel-cron-weekly`
so we run one fewer Railway cron service. Both already wake up Sunday morning UTC,
share the same image, the same DB, the same image build cache. The cleanup is a
sub-30-second SQL `UPDATE` — folding it into the weekly run is a strict win on
cost without changing the cleanup contract.

---

## 1. Current dispatch logic in `railway.toml`

```
startCommand = "sh -c 'case \"$RAILWAY_SERVICE_NAME\" in
    *backfill*) uv run python scripts/run_backfill.py;;
    *cleanup*) uv run python scripts/cleanup_idle_tokens.py;;
    *cron*)    uv run nyc-property-intel-sync;;
    *)         uv run nyc-property-intel;;
  esac'"
```

`case` matches **in order, first-match-wins**.

| Service                                | matches arm   | actual command                                |
| -------------------------------------- | ------------- | --------------------------------------------- |
| `nyc-property-intel-cron-weekly`       | `*cron*`      | `uv run nyc-property-intel-sync`              |
| `nyc-property-intel-cron-cleanup`      | `*cleanup*`   | `uv run python scripts/cleanup_idle_tokens.py`|
| `nyc-property-intel-backfill*`         | `*backfill*`  | `uv run python scripts/run_backfill.py`       |
| `nyc-property-intel` (server)          | default       | `uv run nyc-property-intel`                   |

So the cleanup service is **not** sharing the `*cron*` arm — it has its own
explicit `*cleanup*` arm (added in PR #8). Removing the cleanup service requires
no `railway.toml` change to be safe (the `*cleanup*` arm becomes dead code, but
harmless). We will remove the dead arm in this same PR for tidiness.

## 2. What `nyc-property-intel-sync` resolves to

`pyproject.toml`:
```
[project.scripts]
nyc-property-intel-sync = "nyc_property_intel.sync_runner:main"
```

`src/nyc_property_intel/sync_runner.py` is a tiny shim that prepends `scripts/`
to `sys.path` and calls `sync_all.main()` from `scripts/sync_all.py`.

`scripts/sync_all.py` iterates the dataset registry (filtered by `--tier`,
default 1, or `SYNC_TIER` env), runs each as a `subprocess.run([... sync_delta.py
KEY ...])` with a 1-hour timeout, then emails a summary via Resend if any
dataset failed/warned (or always if `--always-email`).

The weekly cron service (`nyc-property-intel-cron-weekly`) sets
`SYNC_TIER=2` so the same entrypoint runs tier-2 datasets weekly.

Exit codes: `0` = all OK *or* drift warnings (drift exits 0 by design — see
comment in `sync_all.py`); `2` = at least one fatal sync failure.

## 3. What needs to change — three options

### Option A: Run cleanup inline at the end of `sync_all.main()`

After all dataset subprocesses finish (and before `sys.exit`), call
`asyncio.run(cleanup_idle_tokens(dry_run=False))`. The cleanup module already
exits-0-always internally, so any cleanup hiccup leaves the sync's overall exit
code unaffected.

- Pros: no extra subprocess, no extra `uv run`, runs in-process so we share the
  same Python interpreter that's already warm. One file changed (`sync_all.py`).
- Cons: `sync_all.py` now does two conceptually different things. The cleanup
  also runs on tier-1 daily syncs (since `sync_all.py` runs daily for tier-1) —
  arguably fine (idle tokens revoke faster), but it's a behaviour change.

### Option B: Drop the `*cleanup*` arm and find another home

Remove the arm entirely. Implies the cleanup runs from somewhere else (e.g. the
MCP server itself on a timer, or an HTTP endpoint hit by an external scheduler).
Big footprint; reintroduces single-process concerns.

- Pros: forces a clean architectural decision.
- Cons: out of scope for "consolidate into weekly cron." Skip.

### Option C: Invoke `cleanup_idle_tokens.py` as a subprocess from `sync_all.py`

After dataset runs complete, spawn `subprocess.run(["uv", "run", "python",
"scripts/cleanup_idle_tokens.py"])` with a short timeout. Same as Option A but
through a subprocess boundary.

- Pros: process isolation — a hung cleanup can't hang the parent. Output ends
  up in the same Railway log stream.
- Cons: extra `uv run` ⇒ ~2s of dependency resolution overhead. Subprocess
  boundary makes the test mock harder. Doesn't gain us anything Option A
  doesn't already give us, since `cleanup_idle_tokens` already swallows all
  exceptions and exits-0-always.

## 4. Recommendation: **Option A** (inline async call)

`cleanup_idle_tokens.cleanup_idle_tokens(dry_run=...)` is already an async
coroutine that:
- catches every exception,
- caps connections at 2 with a 30s `command_timeout`,
- exits-0-always at the script-`main()` boundary,
- skips internal accounts before any DB write,
- writes are idempotent (`WHERE revoked_at IS NULL` guard).

Calling it inline from `sync_all.main()` is the smallest, safest change. We
gate the call on a tier filter so cleanup only runs on the **weekly tier-2**
pass (matches the prior schedule exactly: Sun 03:00 UTC, ~minutes after the
sync starts; the previous standalone cleanup ran at Sun 04:00 UTC).

Tier-2-only gating is essential to preserve the prior cadence — running it
daily on tier-1 is a behaviour change we don't need or want.

To future-proof against an operator wanting cleanup-off temporarily, we add a
`--skip-cleanup` flag and a `SYNC_SKIP_CLEANUP=1` env var as a kill-switch.

## 5. Test plan

### Unit / integration
- New unit test in `tests/test_cleanup_idle_tokens.py` (or
  `tests/test_sync_all_cleanup.py`) asserting:
  - `sync_all.main()` does **not** call cleanup when `--tier 1`.
  - `sync_all.main()` **does** call cleanup when `--tier 2`.
  - `--skip-cleanup` flag suppresses the call.
  - `SYNC_SKIP_CLEANUP=1` env suppresses the call.
  - Cleanup exception does not change the parent's exit code.
- Existing `tests/test_cleanup_idle_tokens.py` unit tests still pass (they
  should — cleanup module is untouched).
- Integration tests in that file are gated by `@pytest.mark.integration`
  and need a local Postgres — skip on CI / on this PR.

### Local dry-run
```
SYNC_TIER=2 RESEND_API_KEY= DATABASE_URL=postgres://... \
  uv run nyc-property-intel-sync --only hpd_violations --skip-cleanup   # baseline
SYNC_TIER=2 RESEND_API_KEY= DATABASE_URL=postgres://... \
  uv run nyc-property-intel-sync --only hpd_violations                  # cleanup also runs
```
We confirm both cases match expectations in the log output.

### Production verification
After merge, the next Sun 03:00 UTC run should:
- log `running N datasets: ...`,
- log `running cleanup_idle_tokens (post-sync, tier=2)`,
- log a `summary: revoked=K skipped_internal=M dry_run=False` line,
- send the post-sync alert email **and** the cleanup activity is in the same
  Railway log stream.

We verify by reading Railway logs for the weekly cron service after Sunday's
run; if revoked count > 0 we cross-check on the prod DB:
```sql
SELECT count(*)
FROM mcp_tokens
WHERE notes LIKE '%auto-revoked: 21d idle no real calls%'
  AND revoked_at > NOW() - INTERVAL '24 hours';
```

## 6. Migration steps

1. Branch `feat/consolidate-weekly-cleanup`.
2. Code changes:
   - `scripts/sync_all.py`: import + invoke cleanup post-sync when tier == 2,
     add `--skip-cleanup` flag, honour `SYNC_SKIP_CLEANUP`, swallow any
     cleanup exception.
   - `railway.toml`: drop the `*cleanup*` arm (dead after the cleanup service
     is deleted).
   - `tests/test_sync_all_cleanup.py`: new file with mocking-based unit tests.
3. Run pytest locally (skipping `tests/test_security.py` for the unrelated
   import error and `-m "not integration"`).
4. Open PR. **Do not merge** until weekly run window approaches.
5. Merge. Wait for next Sun 03:00 UTC.
6. Verify cleanup ran inside the weekly cron logs.
7. **Only then**: delete the `nyc-property-intel-cron-cleanup` Railway service
   via GraphQL (separate operation, not part of this PR).

### Deletion command (post-merge, after verify)

```bash
# Identify the cleanup service id
railway status --json | jq '.services[] | select(.name == "nyc-property-intel-cron-cleanup")'

# Or via GraphQL (substitute project/env ids):
curl -X POST https://backboard.railway.app/graphql/v2 \
  -H "Authorization: Bearer $RAILWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"mutation { serviceDelete(id: \"<CLEANUP_SERVICE_ID>\") }"}'
```

### Rollback

If the consolidated weekly run fails to revoke (or revokes too eagerly):
- Revert the PR (`git revert <merge-sha>`); the standalone
  `nyc-property-intel-cron-cleanup` service is still alive and will resume
  running on its own schedule.
- No DB rollback needed: cleanup writes are append-only on `revoked_at`/`notes`
  and are already idempotent.
