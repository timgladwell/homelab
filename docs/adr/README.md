# Architecture decision records

An ADR specifies the target design that solves one product problem, and why it
was chosen over the alternatives. The scope runs from "what kind of database"
down to "what shape a function's result takes". ADRs exist so that everyone
working on the system reaches for the same patterns.

What an ADR holds and what it does not, including that it never tracks progress
toward its target, is in [CONTRIBUTING.md](../../CONTRIBUTING.md#adrs). That file
is loaded into every Claude Code session; this one is not, so the rules live
there.

## Format

One file per decision, `NNNN-short-title.md`, with:

- a header listing **Status** (`Proposed`, `Accepted` or `Deprecated`),
  **Date** and **Deciders**;
- **Changelog** — directly after the header, one row per change, newest
  last, so the evolution reads before the detail:

  ```markdown
  ## Changelog

  | Date | By | Description |
  | --- | --- | --- |
  | 2026-10-02 | Tim and Claude | Accepted (#355) |
  ```

  Keep descriptions to one line. If one has to break, GitHub renders `<br>`
  inside a table cell.
- **Context** — the requirements and constraints;
- **Decision**;
- **Consequences** — positive, and negative or risks;
- **Alternatives considered** — what was rejected, and why.

Refer to this repo's ADRs by relative link, and to another repo's by full URL
naming the repo. timbot numbers its ADRs independently, so a bare "ADR 0001" is
ambiguous.

## Numbering and revision

Numbers are assigned in the order decisions are written down here, not the order
they were made. Many decisions in this repo were made before the convention
existed and are not recorded as ADRs yet, so a low number does not mean an early
or foundational decision.

Revising an ADR in place, and retiring one, are covered in
[CONTRIBUTING.md](../../CONTRIBUTING.md#adrs). Each revision adds a changelog row.
