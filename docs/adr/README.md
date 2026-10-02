# Architecture decision records

An ADR specifies part of the system's architecture: given these requirements,
this is the decision, and these are the alternatives rejected. Each ADR addresses
a problem inherent in the system's design. The scope runs
from "what kind of database" down to "what shape a function's result takes".
ADRs exist so that everyone working on the system reaches for the same patterns.

They are not where current state is described (that is `docs/*.md`), where a
procedure is written down (`docs/runbooks/`), or where work is queued (issues).

## Format

One file per decision, `NNNN-short-title.md`, with:

- a header listing **Status** (`Proposed`, `Accepted` or `Deprecated`),
  **Date** and **Deciders**;
- **Context** — the requirements and constraints;
- **Decision**;
- **Consequences** — positive, and negative or risks;
- **Alternatives considered** — what was rejected, and why;
- **Changelog** — one row per change, newest last:

  ```markdown
  ## Changelog

  | Date | By | Description |
  | --- | --- | --- |
  | 2026-10-02 | Tim and Claude | Accepted (#355) |
  ```

  Keep descriptions to one line. If one has to break, GitHub renders `<br>`
  inside a table cell.

Refer to this repo's ADRs by relative link, and to another repo's by full URL
naming the repo. timbot numbers its ADRs independently, so a bare "ADR 0001" is
ambiguous.

## Numbering and revision

Numbers are assigned in the order decisions are written down here, not the order
they were made. Many decisions in this repo were made before the convention
existed and are not recorded as ADRs yet, so a low number does not mean an early
or foundational decision.

Revising an ADR is expected, not a failure. If the chosen solution turns out to
be wrong, the ADR is revised in place: it is still the same problem and the same
discussion, so it is not superseded by a new ADR. Each revision adds a changelog
row. An ADR is `Deprecated` only when its problem has gone out of the system's
scope.
