# Packaging the triage toolkit for coworkers — parked plan

**Status:** ① `fx-bug-toolkit` **shipped** (public, v0.1.5). Triage + dashboard
now being folded into it as a **single plugin** (see the 2026-06-04 decision
below, which supersedes the two-plugin scope).

**Last updated:** 2026-06-04

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

## Decision update (2026-06-04): collapse to ONE plugin + lazy dashboard

This supersedes the two-plugin scope below. The two-plugin split (① `fx-bug-toolkit`
+ ② `fx-triage`) was, in the end, mostly **ceremony** for a personal/small-team
toolset — a second repo, manifest, CI, version line, and a `dependencies` edge —
and the *one* thing it was really buying (not forcing the dashboard's heavy deps
on investigate-only users) can be solved inside a single plugin with a **lazy
install**. So:

**Everything lives in one plugin: `fx-bug-toolkit`** (already shipped).
- It gains the **triage skills** (`/triage`, triage-apply-feedback) and a
  **`/triage-dashboard` launcher** skill.
- The **dashboard becomes a pip-installable app** (`pyproject.toml`), **bundled
  inside the plugin** (e.g. `dashboard/` or `src/triage_dashboard/`).
- `init` is **unchanged** — it installs only the core investigation CLIs. It does
  **not** install `fastapi`/`uvicorn`, so investigate-only users pay nothing.
- **Lazy, consent-gated bootstrap on first `/triage`**: the triage skill checks
  for a managed venv (e.g. `~/.fx-bug-toolkit/venv`); if absent, it asks before
  creating it + `pip install`-ing the bundled dashboard app, then runs `uvicorn`.
  Subsequent runs skip the bootstrap. (Honors the toolkit's existing "never
  install without confirming first" rule.) Re-bootstrap when deps change on
  `claude plugin update`.

**Why one plugin is fine now (and the line that actually mattered):**
- The decision was never really "1 vs 2 plugins" — it was *"are the dashboard's
  heavy deps mandatory or on-demand."* Lazy install makes them on-demand, which
  is the whole win, without a second plugin.
- The "is it a server?" distinction is a red herring — the bundled viewer
  (`serve.py`) is also a server. The real line is **deps + side effects**:
  `serve.py` is stdlib + read-only; the dashboard needs a framework stack and
  actively queues/applies work, so it gets a venv and a lazy bootstrap rather
  than riding in for free.

**Correction to a prior assumption:** Claude Code **does** support inter-plugin
dependencies — `plugin.json` can declare `"dependencies": ["other-plugin"]` and
they auto-install (≥ 2.1.110), and one marketplace can host multiple plugins.
(Verified 2026-06-04 against the docs.) This makes the old "no auto-install →
must document install order" worry moot — but it's also now irrelevant, since
there's only one plugin.

**Revised work items (replaces Phase 3's two-plugin framing):**
- [ ] Make `triage-dashboard` **pip-installable** (finalize `pyproject.toml`,
      entry point, static/templates as package data).
- [ ] **Bundle** the dashboard app into the `fx-bug-toolkit` repo/plugin.
- [ ] Add **`/triage`** + **`/triage-dashboard`** skills to the plugin; the
      latter is the launcher (the `serve.py`/`browse` pattern, but it first
      ensures the venv).
- [ ] Implement the **lazy venv + pip bootstrap** (consent-gated; idempotent;
      cross-platform — heed the MSYS2/PATH/zshenv lessons from v0.1.2–0.1.4).
- [ ] **Unify the data dir** — there are currently three (`~/firefox-triage/`,
      `~/firefox-bug-investigation/`, and the plugin default
      `~/.fx-bug-toolkit/bug-investigation`). Pick one convention so the triage
      skills, the dashboard, and the investigation viewer all read/write the same
      place. This is the real integration contract and the prerequisite for the
      rest.
- [ ] Document the **data contract** (the `pending/*.json` schema +
      `claude-queue.jsonl` action format + the investigation file schema) — now
      with three consumers (triage skills, dashboard, browse viewer).

---

## Scope decision (2026-06-03): two plugins only — **SUPERSEDED** (see 2026-06-04 update above)

We categorized all 27 personal skills and decided **what NOT to expose** is as
important as what we do. Only two plugins are in scope, plus the wiki as an
optional companion:

- **① `fx-bug-toolkit`** — the per-bug investigation cluster. **The MVP;
  ships first, alone.** Broadly useful to any coworker doing A/V bug work.
- **② `fx-triage`** — the opinionated weekly A/V triage *process* + the
  dashboard. Layered on top of ①; only for a triage co-owner.

**Explicitly dropped from this effort** (not exposed): the
implementation/review clusters and everything that only served them —
`firefox-implementation`, `verify`, `try-push`, `ci-failure-analysis`,
`sec-approval`, `review-patch`, `review-feedback`, `git-worktree-sync`,
`clear-done-worktrees`, `taskboard`, `commit-rules`, `code-comment-rules` — plus
the standalone/personal skills `playwright`, `mozdata`, `auto-update-my-md`,
`project-plan`, `setup-dev-flow`, and the unidentified `firefox-manager`.

```
┌─────────────────────────────────────────────────────────────────────┐
│  ②  fx-triage   — opinionated A/V triage process                      │
│      triage · triage-apply-feedback · [dashboard repo]                │
│      (for a triage co-owner only)                                     │
└─────────────────────────────────────────────────────────────────────┘
                    │  hard edge: dispatches /bug-start --triage-mode
                    ▼  (Layer ② REQUIRES Layer ① installed)
┌─────────────────────────────────────────────────────────────────────┐
│  ①  fx-bug-toolkit      ★ THE MVP — ships first, alone ★              │
│                                                                       │
│      bug-start (hub)                                                  │
│        ├── analyze-profile        ├── update-investigation            │
│        ├── check-firefox-log      ├── spec-check                      │
│        ├── download-guard   (bundled IN — not a separate plugin)      │
│        ├── source-links     (passive rule, for nicer output)          │
│        └── gecko-navigator  [agent]                                   │
└─────────────────────────────────────────────────────────────────────┘
                    ┊  optional, gated on INDEX.md (degrades silently)
                    ▼
        ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
        ┆  firefox-wiki  (already a plugin) + content  ┆   optional
        ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
```

### The two plugins at a glance

| | **① fx-bug-toolkit** | **② fx-triage** |
|---|---|---|
| **Skills** | bug-start, analyze-profile, check-firefox-log, spec-check, update-investigation, download-guard, source-links + gecko-navigator agent | triage, triage-apply-feedback + dashboard repo |
| **Audience** | any coworker doing A/V bug work | a triage **co-owner** (shares the weekly process) |
| **Depends on** | nothing required (wiki optional) | **requires ①** |
| **Ship order** | **v1** | v2, layered on top |

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

## Dependency findings (verified this session by reading the call graph)

The full transitive dependency graph for the in-scope plugins. Edges are: `──▶`
invokes/dispatches (hard), `┄▶` optional (degrades silently), and the dashboard's
file-contract coupling. Leaf nodes on the right are the external CLIs/services
each skill assumes present.

```
②  fx-triage
   triage ─────────────────────────────────────────────┐ CLIs: bmo-to-md, jq,
     │  writes ~/firefox-triage/pending/*.json          │       git, python3
     │  drains  ~/firefox-triage/claude-queue.jsonl      │
     │                                                   │
     ├──▶ /bug-start --triage-mode  (hard edge into ①)   │
     ├──▶ /download-guard                                │
     └┄▶ /firefox-wiki:lookup|add        [optional]      │
   triage-apply-feedback                                 │
     └┄▶ /firefox-wiki:add               [optional]      │
                                                         │
   [dashboard repo] ── reads pending/*.json + investigation files,
       appends claude-queue.jsonl ; never spawns Claude/CLI directly.
       Only coupling = the ~/firefox-triage/ data contract.
─────────────────────────────────────────────────────────────────────────────
①  fx-bug-toolkit
   bug-start (hub) ── writes ~/firefox-bug-investigation/
     ├──▶ analyze-profile ─────────────── profiler-cli  ┄▶ wiki [opt]
     ├──▶ check-firefox-log               (self-contained)
     ├──▶ spec-check ───────────────────── searchfox-cli, mach
     ├──▶ update-investigation            (self-contained)
     ├──▶ download-guard ───────────────── bmo-to-md  (bundled IN ①)
     ├··▶ source-links                    (passive rule — nicer output)
     ├──▶ gecko-navigator  [AGENT] ─────── searchfox-cli, mcp__moz__*
     ├──── CLIs: bmo-to-md, searchfox-cli, profiler-cli, mach+checkout,
     │           git, mcp__moz__get_bugzilla_bug
     └┄▶ /firefox-wiki:lookup|add         [optional, gated on INDEX.md]
                                                  ┊
        ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
        ┆ firefox-wiki plugin + ~/firefox-wiki content ┆   optional companion
        ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
```

- **`download-guard` is the only utility the MVP truly needs**, and it's a
  *behavioral* dependency (consumers say "follow the `/download-guard` rule" —
  no programmatic call). Without it nothing crashes, but the download safety
  gate silently no-ops. Because Claude Code plugins **cannot auto-install each
  other**, we **bundle download-guard inside ①** rather than externalizing it —
  a separate "utility" plugin would create a silent cross-plugin failure.
- **A generic "utility" plugin is the wrong cut.** "Utility" is a theme, not a
  coupling. The candidate utilities serve different clusters: `download-guard`
  → investigation (①), the rule skills (`commit-rules`, `code-comment-rules`,
  `source-links`) → implementation (dropped), `playwright` → nobody (leaf).
- **`source-links`** has only one *explicit* consumer (`firefox-implementation`,
  now dropped). It stays in ① as a **passive quality rule** — bug-start writes
  investigation files with hyperlinked sources — not as a hard dependency.
- **`playwright` is a true leaf** — zero skills reference it. Out of scope.
- **The dashboard is a thin shell.** It never spawns Claude or any CLI; it only
  reads `~/firefox-triage/` + `~/firefox-bug-investigation/` and appends to
  `claude-queue.jsonl`, which a human then drains. Its only real coupling is the
  `~/firefox-triage/` **data contract** (the `pending/*.json` schema + the
  queue action format) — documented as the Phase-3 deliverable below.
- **External CLIs/services both plugins assume present:** `bmo-to-md`,
  `searchfox-cli`, `profiler-cli`, `mach` + a mozilla-central checkout, the
  `moz` MCP server, and `jq`/`git`/`python3`. The `moz` MCP server has **no
  discoverable config file** — a coworker won't get it for free; it needs a
  documented install step.

---

## DONE

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
- **Scope cut to two plugins** (2026-06-03) — see Scope decision above. Dropped
  the implementation/review clusters and standalone/personal skills.

---

## Open questions / decisions for next time

1. **Wiki contribution model** — PR-reviewed vs direct-push. Gates the wiki
   being multi-contributor-safe.
2. **Dashboard model** — each coworker runs their own local instance (all
   pointing at the shared wiki) vs one shared dashboard. Changes the dashboard
   work a lot. Only matters once we ship plugin ②.
3. **Native plugin dependency?** — Confirmed assumption: Claude Code does **not**
   support a plugin declaring/auto-installing another. The plan therefore relies
   on graceful degradation + a documented "install ① first" step for the ②→①
   edge, not auto-install. (Re-verify before relying on multi-plugin installs.)
4. **`firefox-manager` is unidentified** — empty description; decide if it's even
   relevant before it can be categorized. Currently out of scope.

---

## Work items when we resume (not yet done)

**Phase 0 — wiki multi-contributor hygiene (foundation; needed regardless)**
- [ ] gitignore per-user telemetry in the `firefox-wiki` content repo:
      `usage-log.jsonl` (974 lines, all stamped with personal email — would
      cause constant merge conflicts), and likely `lint-log.json`,
      `verify-report.md`. Content shared; logs local.
- [ ] one-time private-data scan of wiki content (sec keywords, bug numbers,
      internal URLs). Note: current content already has **0** `sec-*` pages.

**Phase 1 — sanitize plugin ① (fx-bug-toolkit; single source of truth, no hard-copy)**
- [ ] strip the `/auto-update-my-md` push step in `bug-start` (6c) — it pushes
      investigation files to a personal GitHub repo
- [ ] parametrize hardcoded paths: `profiler-cli` location in `analyze-profile`
      (`~/projects/profiler-cli/dist/index.js`), output dir
      `~/firefox-bug-investigation/`, default log path in `check-firefox-log`
- [ ] capture the extraction as a small script + checklist (so re-sync is
      "re-run + review diff", never manual copy-paste forever)
- [ ] document external CLI deps the coworker machine needs:
      `jq`, `python3`, `git`, `searchfox-cli`, `profiler-cli`, `bmo-to-md`,
      `mach` + a mozilla-central checkout, the `moz` MCP server — and which
      feature degrades without each. Note the `moz` MCP server has no
      discoverable config; document how to install/register it.

**Phase 2 — package plugin ① + the wiki companion**
- [ ] a marketplace listing `fx-bug-toolkit` (+ the `firefox-wiki` plugin
      as a separate, optional plugin in the same marketplace)
- [ ] bundle `download-guard` and the `gecko-navigator` agent inside ①
- [ ] ship ① alone as v1; validate a coworker can install + investigate a bug
      with zero personal config

**Phase 3 — plugin ② (fx-triage + dashboard); only after ① lands**
- [ ] decide the Open-question #2 dashboard model (per-user local vs shared)
- [ ] parametrize personal GitHub URLs in the dashboard templates (the "Triage"
      title link, investigation links) and serve investigations locally rather
      than from a personal repo
- [ ] document the `~/firefox-triage/` data contract both halves share — the
      `pending/*.json` schema and the `claude-queue.jsonl` action format
- [ ] document the hard **②→① install order** (no auto-install)
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
  graph is sparse. ("Utility" is a theme — that's why we rejected a utility
  plugin and bundled `download-guard` into ① instead.)
- **Never hard-copy + manually-sync-forever.** Single source of truth (the
  existing repos) + a scripted, sanitized extraction.
- **Wiki = optional accelerator, not a hard dependency.** Already true for the
  toolkit after this session's gating work. This also sidesteps the
  unconfirmed native-dependency question — nothing hard-requires the wiki.
- **"Optional" is a skip-risk in skill text.** Gate on a verifiable fact
  (`test -f .../INDEX.md`) with a hard `MUST` on the installed branch, not on
  the word "optional".
- **No auto-install between plugins.** Behavioral/cross-plugin edges fail
  *silently*. Keep tightly-coupled pieces in the same plugin (download-guard in
  ①); for the unavoidable ②→① edge, document install order.
- **Investigations stay private; only the wiki is shared.**

---

*Related: this repo's README "triage loop" section; `firefox-wiki-plugin/docs/
skill-attribution.md`; `firefox-wiki-plugin/scripts/wiki-relevant-skills.txt`.*
