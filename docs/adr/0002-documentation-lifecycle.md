<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# ADR 0002 — Documentation lifecycle

- **Status:** Accepted
- **Date:** 2026-09-29

!!! note "Frozen decision record"
    This page records why the documentation is organised the way it is. The living rules a writer
    follows are in [Changing the docs](../reading.md#changing-the-docs).

## Context

The docs site had grown into a usable operator manual, but the same fact lived on several pages
(the quick start, multi-tenancy, the new-connector checklist, quality commands, MCP images) and the
copies had begun to disagree. A decision record sat in the MCP navigation next to the how-to it
explained, and no page said which page owned current behaviour.

stacklok's [mecatl](https://github.com/stacklok/mecatl/tree/main/docs) solves the same problem by
giving each job one home: an audience front door, a progressive reading map, living architecture
pages, frozen ADRs, and operator task pages.

## Decisions

| Decision | Why |
|---|---|
| **One fact, one owner.** Every fact has one owning page. Other pages link to it and do not restate it. | Copies drift. Two of them already had. |
| **Change review:** pick the owning page, check the claim against the code, edit that page in place, and delete any copy you find. | Keeps the rule enforceable in review, not just stated. |
| **Living pages describe what is true now.** They carry no `Status:` line, no dated history, and no "fixed along the way" lists. Bug chronology and repair notes stay in the PR and `CHANGELOG.md`. | A living page that narrates history stops saying what is true. |
| **Decision records are frozen** under `docs/adr/`. A later change is a new ADR with `Supersedes:`. The old record gets only a `Superseded by:` back-pointer. | The *why* at the time must survive later edits. |
| **Plans are not evidence.** A plan or spec describes intent. It does not show that the behaviour shipped. | Stops docs from describing features that never landed. |
| **One MkDocs site.** Operator tasks, contributor architecture and decisions share `docs/`, and the navigation separates them. ADRs sit only under *Decisions*. | mecatl's separate `user-docs/` tree is not needed at this size. The ownership rules are what matter. |
| **Moved pages become short redirect stubs**, left out of the navigation. | Old links, including external ones, keep working. |
| **Checks over prose.** `tests/test_docs_lifecycle.py` enforces the lifecycle split and checks the hand-kept lists (public API, package inventory) against the code. `mkdocs build --strict` still catches dead links. | A check does not drift the way a sentence does. |

## Consequences

- `docs/index.md` routes readers by audience and links to the [reading map](../reading.md). It does not restate guides.
- The root `README.md` is a short product page and points at the site.
- `docs/architecture.md` is the overview; `docs/architecture/*.md` holds the domain, seams and tenancy detail.
- Adding a decision means adding an ADR and a row in the [ADR index](index.md).
