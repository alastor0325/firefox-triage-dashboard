# Packaging the triage toolkit for coworkers — parked plan

**Status:** parked / not started. This is a record of the design discussion so
we can resume cold. Nothing here is committed-to yet except the few items
marked **DONE**.

**Last updated:** 2026-06-01

---

## Goal / vision

Share the A/V triage workflow with coworkers so they can both **use** it and
**contribute back to the shared wiki** — creating a flywheel where everyone's
triage gets faster as the wiki grows.

```
coworker triages bugs (skills + dashboard)
   → consults wiki before reading code
   → discovers + verifies new facts
   → contributes back to wiki
   → wiki gets richer → everyone's next triage is faster
```

The skills/dashboard are how people *use* it; the **shared wiki is what makes
it compound**. That is the point — not any single skill.

---

## The system = 4 layers

| Layer | Repo | Self-contained? | Sharing difficulty |
|---|---|---|---|
| A. Wiki engine | `firefox-wiki-plugin` | Yes — no hardcoded paths | Easy (already a plugin) |
| B. Wiki content | `firefox-wiki` (~60 component pages, specs, patterns) | the valuable asset | Medium — private-data + multi-user |
| C. Triage/investigation skills | `Claude-Skills` (bug-start, analyze-profile, …) | hardcoded deps | Hard (portability work) |
| D. Triage dashboard | `firefox-triage-dashboard` | standalone FastAPI | Medium |

---

## Data-ownership split (resolves the "don't expose my files" worry)

| Asset | Shared? | Why |
|---|---|---|
| Wiki content (component pages, patterns, specs) | **Shared** — read + write | the compounding knowledge |
| Usage logs (`usage-log.jsonl`, per-user telemetry) | **Local per-user** | personal; causes merge conflicts if shared |
| Investigation files (`~/firefox-bug-investigation/`) | **Local per-user** | private working notes — never need sharing |

Coworkers contribute **curated wiki knowledge**, not raw investigations. The
only shared write-target is the wiki.

---

## Decided: the MVP package = "Firefox A/V bug investigation toolkit"

Package **by coupling (the call-graph), not by theme.** Skills that invoke each
other must ship together (no native auto-dependency — see open questions).

**Bundle (the investigation/analysis cluster):**
- `bug-start` (the hub; triage-mode + deep mode)
- `analyze-profile` (profiles — called by bug-start)
- `check-firefox-log` (logs — called by bug-start)
- `spec-check` (spec conformance; also useful standalone)
- `source-links` (rule — link formatting)
- `gecko-navigator` (agent — architecture/flow)
- `update-investigation` (iterate the findings — completes bug-start's loop)

**`firefox-wiki` ships as an OPTIONAL extension**, not a hard dependency. With
it: faster starts (lookup) + compounding (contribute back). Without it: the
toolkit still fully works (just searches code directly).

**Explicitly OUT (implementation-level, not investigation):**
firefox-implementation, verify, commit-rules, jj-split, git-worktree-sync,
review-patch, review-feedback, sec-approval, try-push, ci-failure-analysis,
reorganize-patches, taskboard.

**The opinionated `triage` orchestrator + `triage-apply-feedback` + the
dashboard** are a *second* package layered on top — only for whoever actually
shares the weekly A/V triage *process*. The `triage` skill encodes our specific
policy (§1a/§1b/§1c tabs, component routing, media-alerts conventions), so it's
gold for a co-owner and over-specific baggage for someone who just wants good
per-bug tooling.

### Two framings to choose between later
- **A. "Run my triage process"** — full bundle + dashboard + triage orchestrator. For a triage co-owner.
- **B. "Better A/V bug tooling + shared wiki"** — the investigation toolkit above. Broadly useful, lower risk. **The MVP leans B.**

---

## DONE this session

- **Wiki made an optional extension via presence gates.** `bug-start` (step 3
  lookup, §6b write-back) and `analyze-profile` (step 3 lookup, step 6
  write-back) now gate every wiki touchpoint on a deterministic check:
  ```bash
  test -f "${WIKI_PATH:-$HOME/firefox-wiki}/INDEX.md" && echo WIKI_INSTALLED
  ```
  Installed → `MUST` use it (not "optional", which Claude would skip).
  Absent → skip silently, no error. `check-firefox-log`, `spec-check`, and the
  `gecko-navigator` agent needed no change (they never invoke the wiki).
  Committed to Claude-Skills `d9e4536`.
- **Verified** the gate live: present → uses wiki; absent (INDEX.md moved
  aside) → skips silently; custom `WIKI_PATH` → honored. Wiki restored
  byte-for-byte after the test.
- **`WIKI_PATH` finding:** set *nowhere* (not env, profiles, settings.json, or
  the plugin). Everything defaults to `~/firefox-wiki`. Coworkers need zero
  config — clone the wiki to `~/firefox-wiki` and it's found; set `WIKI_PATH`
  only to relocate.

---

## Open questions / decisions for next time

1. **Framing A vs B** — share the opinionated triage process, or just the
   investigation toolkit? (MVP assumes B.)
2. **Shared wiki contribution model** — PR-reviewed vs direct-push. Gates the
   wiki being multi-contributor-safe.
3. **Dashboard model** — each coworker runs their own local instance (all
   pointing at the shared wiki) vs one shared dashboard. Changes the dashboard
   work a lot.
4. **Native plugin dependency?** — Confirm whether current Claude Code supports
   a plugin declaring/auto-installing another. To my knowledge it does NOT;
   the plan therefore relies on graceful degradation + a bootstrap `init`
   script, not auto-install. **Verify before relying on "many small plugins".**

---

## Work items when we resume (not yet done)

**Phase 0 — wiki multi-contributor hygiene (foundation; needed regardless)**
- [ ] gitignore per-user telemetry in the `firefox-wiki` content repo:
      `usage-log.jsonl` (974 lines, all stamped with personal email — would
      cause constant merge conflicts), and likely `lint-log.json`,
      `verify-report.md`. Content shared; logs local.
- [ ] one-time private-data scan of wiki content (sec keywords, bug numbers,
      internal URLs). Note: current content already has **0** `sec-*` pages.

**Phase 1 — sanitize + extract the toolkit (single source of truth, no hard-copy)**
- [ ] strip the `/auto-update-my-md` push step in `bug-start` (6c) — it pushes
      investigation files to a personal GitHub repo
- [ ] parametrize hardcoded paths: `profiler-cli` location in `analyze-profile`
      (`~/projects/profiler-cli/dist/index.js`), output dir
      `~/firefox-bug-investigation/`, default log path in `check-firefox-log`
- [ ] capture the extraction as a small script + checklist (so re-sync is
      "re-run + review diff", never manual copy-paste forever)
- [ ] document external CLI deps the coworker machine needs:
      `jq`, `python3`, `git`, `searchfox-cli`, `profiler-cli`, `bmo-to-md`,
      the `moz` MCP server — and which feature degrades without each

**Phase 2 — package structure**
- [ ] a marketplace listing the toolkit plugin (+ the `firefox-wiki` plugin as
      a separate, optional plugin in the same marketplace)
- [ ] decide: one toolkit plugin (recommended — fewest cross-plugin edges) vs
      several smaller plugins

**Phase 3 — dashboard shareable (only if we go framing A / share the dashboard)**
- [ ] parametrize personal GitHub URLs (the "Triage" title link, investigation
      links) and serve investigations locally rather than from a personal repo
- [ ] document the `~/firefox-triage/` data contract both halves share
- [ ] a `dashboard init` bootstrap that wires everything: check deps, install
      plugins (via `claude plugin` CLI if it exists, else careful merge into
      `~/.claude/settings.json` — the `extraKnownMarketplaces` + `enabledPlugins`
      structure already in use), clone the shared wiki to `$WIKI_PATH`, create
      data dirs, then prompt a Claude Code restart (hooks.json changes need a
      reload; script edits are read fresh)

**Phase 4 — operate as a team**
- [ ] wiki contribution review cadence; verify/lint ownership
- [ ] optional team-level stats (the stats script already supports a per-user
      `user` field)

---

## Key principles to remember

- **Package by coupling, not theme.** Cut plugin boundaries where the call
  graph is sparse.
- **Never hard-copy + manually-sync-forever.** Single source of truth (the
  existing repos) + a scripted, sanitized extraction.
- **Wiki = optional accelerator, not a hard dependency.** Already true for the
  toolkit after this session's gating work. This also sidesteps the
  unconfirmed native-dependency question — nothing hard-requires the wiki.
- **"Optional" is a skip-risk in skill text.** Gate on a verifiable fact
  (`test -f .../INDEX.md`) with a hard `MUST` on the installed branch, not on
  the word "optional".
- **Investigations stay private; only the wiki is shared.**

---

*Related: this repo's README "triage loop" section; `firefox-wiki-plugin/docs/
skill-attribution.md`; `firefox-wiki-plugin/scripts/wiki-relevant-skills.txt`.*
