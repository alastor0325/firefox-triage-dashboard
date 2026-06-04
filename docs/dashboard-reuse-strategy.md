# Reducing the multi-tool maintenance burden — reuse strategy (parked discussion)

**Status:** discussion / parked. A record of the thinking so we can resume cold.
Nothing here is committed-to; it's the *lens* we want to apply when deciding how
much shared infrastructure to build, not a work plan.

**Last updated:** 2026-06-04

---

## The question that started this

I keep building small local web dashboards over a data dir (the investigation
**viewer**, the **triage dashboard**, and several others). Each re-implements the
same basics — a server, data loading, UI components (cards, search, chips,
deep-links), and a **test harness**. Rewriting those every time is tiring. Should
I merge them, build a shared framework, or rethink the approach entirely? (I'm
willing to rewrite from scratch, even in another language.)

---

## What we ruled out, and why

- **Merge the viewer's code into the triage repo to make "one general dashboard
  repo."** No — they have *different stacks* (the viewer is zero-dep vanilla-JS +
  stdlib `serve.py`; triage is FastAPI + uvicorn + Jinja + htmx + SSE). Merging
  the code means either rewriting the viewer onto FastAPI (re-adding deps to the
  lean browse path) or hosting two stacks in one repo (messy). The triage app is
  a *concrete app*, not a framework — making it "the general one" couples every
  future tool to triage's specifics.
- **Build a heavyweight "dashboard framework."** A framework *inverts control* (it
  calls your code) → high lock-in, expensive to build well, hard to evolve. If we
  share anything, prefer a **library/kit** (your apps call it) — lower risk,
  grown by extraction from real usage.

---

## Core principle: separate the **durable data layer** from the **disposable presentation layer**

Each new tool is expensive because we treat each dashboard as a *product* (own
server, components, loaders, tests). But across all of them the only thing that's
genuinely *ours and durable* is the **data** (investigations, triage drafts,
status). The UI is — and should be — disposable and swappable.

Right model: **one data substrate, many thin views over it.** Get this right and
the per-dashboard cost collapses: a "new dashboard" becomes "a new view," not "a
new product." The hard, must-be-correct part (data) is shared and tested once.

---

## The AI-era reframe (the key insight)

The instinct to "reduce duplication via abstraction" is calibrated for an era of
**expensive human writing**. With Claude, generating a whole dashboard or a fresh
test suite is cheap — so the *"I don't want to retype this"* motivation for DRY
and frameworks is largely **dead**. A lot of received engineering wisdom should be
re-examined on those grounds.

**But DRY was only partly about typing.** The costs that *don't* collapse with
cheap generation — and some that get worse:

- **Consistency / change-propagation.** N independently-generated tools *drift*.
  Fixing a bug or changing a convention means knowing all N exist, that they all
  need it, and verifying each. Cheap edits ≠ cheap consistency. That's human.
- **Review / trust / audit.** This cost scales with **surface area**, not writing
  speed (cf. the pre-public security audit we ran on the triage repo). More
  codebases = more to read, trust, and audit. The scarce resource moved from
  *keystrokes* to *attention*.
- **Multiplied bug surface.** Three independently-written SSE loops = three places
  it can be subtly wrong, three test suites you must trust encode the same intent.
- **The data contract.** Every tool reads/writes the same data. If the schema is
  implicitly re-derived per codebase, Claude will generate three *slightly
  different* readings, and a schema change breaks them three different silent
  ways. This is the duplication that genuinely hurts — and where "just let the AI
  redo it" actively backfires.

**So the rule for the AI era:**

> **Share the correctness-critical substrate** — the **data contract/store** and
> the handful of **invariants** that define "correct" (e.g. *never render a
> comment marked secret*). **Let the disposable presentation be cheaply, even
> divergently, regenerated.**

This is a sharper version of "data vs presentation," and it *vindicates* the
instinct not to frameworkize the UIs: keep dashboards as separate codebases Claude
maintains, but pin the data layer + a tiny set of safety/invariant tests as the
single source of truth.

---

## The axis that sets how far to lean "just duplicate": **stakes**

- **Private / throwaway / single-user** → duplicate freely, regenerate, never
  abstract. The cheap-generation argument fully wins.
- **Public / shared / multi-user / long-lived / security-sensitive** (where the
  triage tooling is heading, with coworkers) → the consistency + audit burden is
  real, so a shared *hardened core* for the cross-cutting and safety bits pays off.

Lean per-tool by stakes, not by a blanket rule.

---

## Failure mode to watch: **sprawl**

Cheap creation invites lots of half-understood tools — individually fine,
collectively an inconsistent surface nobody can fully vouch for. The new
discipline isn't "don't repeat code"; it's **"don't accumulate surface you can't
account for, or keep consistent on the things that matter."**

---

## Adopt vs build (evaluate before building anything)

The cheapest component/test to maintain is the one you never wrote. Before
building a kit *we* then maintain forever, weigh adopting a maintained tool:

| Tool | Fits when dashboards are… | Caveat |
|---|---|---|
| **Datasette** | browse / search / filter / link records (the *viewer* is exactly this) | data in SQLite; custom views/actions via its plugin API |
| **Streamlit / Panel / Gradio** | quick data-exploration UIs, Python | opinionated interaction model; awkward for custom layout / SSE / actions |
| **Observable Framework / static-site gen** | read-only dashboards from data | not for write/interactive actions |
| **One self-built app, view-plugin architecture** | genuinely bespoke: custom layout + live updates + actions (the *triage* board) | we own all of it, including the tests |

---

## Current lean (tentative)

1. **Don't build a dashboard framework.** Keep the UIs as separate, AI-maintained
   codebases.
2. **Nail the data contract once** (a single store — local SQLite is the natural
   fit — or a tightly-specified file schema). This is the real IP.
3. **Keep a small shared set of invariant/safety tests** (so all tools agree on
   what "correct" means — especially the privacy/sec invariants).
4. **Adopt for the browse-y tools** (Datasette-class) where it fits; **build one
   bespoke app** only for the interactive/action-bearing ones.
5. **Own** the data contract + the test kit; **inherit** everything else.

---

## Open questions (to resume on)

1. **What are the "several dashboards," and how interactive is each** — mostly
   read-only browse/search, or do several have actions/writes/live updates like
   triage's apply-queue? (Decides adopt-vs-build per tool.)
2. **Which felt most duplicated** — UI components, backend plumbing, or the test
   harness? (The test harness is the highest-ROI, stack-independent first
   extraction.)
3. **One stack or several?** Since a rewrite is on the table, do we standardize on
   one stack for the interactive tools (likely FastAPI), and keep/retire the
   zero-dep viewer?
4. **SQLite as the substrate?** Would moving the data layer to a local SQLite
   (vs scattered JSON/markdown) make the "one store, many views" model real — and
   unlock Datasette for the browse-y tools for free?

---

## Relationship to `coworker-packaging-plan.md` (proposed — to confirm)

- That doc is the **PLAN**: concrete, near-term, committed — *how* to package and
  ship the investigation/triage toolkit (one plugin, lazy dashboard, pip, the
  pre-public audit, the data-contract work item). It has work items and a status.
- This doc is the **PRINCIPLES**: longer-horizon strategy for not drowning in
  tool maintenance. It **informs but does not block** the plan.
- **They meet at the data contract.** The plan's prerequisite work item ("unify
  the data dir + document the schema") *is* this doc's central recommendation
  ("the one durable thing worth sharing"). That work item is where the principle
  becomes action.
- **Near-term implication of these principles on the plan:** don't build a
  dashboard framework as part of packaging; do treat the **data contract as the
  load-bearing deliverable**; keep the viewer and dashboard as separate UIs (cheap
  to maintain/regenerate) and unify only the **data**.
