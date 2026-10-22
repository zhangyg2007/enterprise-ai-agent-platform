# M11 Enterprise Workflows Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline, TDD, separate verification and independent final review. Deliver stages honestly; T1 alone is not M11 completion.

**Goal:** Three enterprise cases with approval, transaction-safe effects, idempotency and durable graph recovery.
**Architecture:** Trusted actor -> deterministic draft -> approval -> version-rechecked atomic execution -> immutable receipt; graph checkpoints separate from business transactions.
**Tech Stack:** Python standard-library SQLite fixture first, genuine LangGraph persistent saver next, formal PostgreSQL adapter/checkpointer on Ubuntu separately.
**Spec:** ../specs/2026-10-22-m11-enterprise-workflows-design.md

## Global Constraints

- Do not change M10's read-only whitelist or claim its principal authenticates an API.
- Synthetic SQLite ledger is a separate new database; no M02 business writes here.
- Tenant/owner/manager rules checked before returning requests; thread_id is not a credential.
- Strict fields, no arbitrary SQL, Decimal money, positive integers excluding bool.
- No effect before approval or after rejection; one atomic effect/receipt per idempotency key.
- Source revision changed after draft -> conflict, no write; checkpoint restart is not business idempotency.
- Do not mark whole chapter complete until T1–T3 + tutorial + review; real-server acceptance stays separate.

## Review Focus

Cross-owner key reuse, cross-tenant IDs, self-approval, reversing decisions, replay after resource version changes, two approvals consuming same balance/inventory, invalid decimal/nonfinite/bool, atomic rollback, typo paths creating empty DB, output overwrite.

### T1: Durable synthetic business ledger

Files: workflow_store.py, test_workflow_store.py; T1 lab/runbook evidence and Filelist.
- [x] RED three hand-computed cases, reject/no-write, replay/same-key conflict, owner/tenant/manager, revision conflict, restart, bad args/new-file protection.
- [x] Implement FixtureActor/create_store/draft/get_request/decide/execute/read_resource/audit inspection with parameterized SQL and BEGIN IMMEDIATE.
- [x] GREEN targeted10/full237passed; runnable lab independent-process replay; independent T1 review no actionable findings. T2/T3 remain pending.

### T2: Genuine persisted LangGraph approval

Files: workflow_graph.py, workflow_cli.py, tests; fixed sqlite saver requirements.
- [ ] RED pause/reopen/resume, owner guard before checkpoint, reject no effect, post-effect crash/retry single receipt, per-thread concurrency guard.
- [ ] GREEN actual SqliteSaver/interrupt/Command CLI process restart evidence, not in-memory substitute.

### T3: Formal server path and chapter delivery

Files: PostgreSQL adapter/migration and checkpointer setup, contract/integration tests, tutorial11/runbook/validation, README/ROADMAP/plan/Filelist.
- [ ] Explicit new schema/roles, session-auth mapping, transaction/receipt invariants and safe migration instructions; no unapproved live mutation.
- [ ] Actual local framework tests, SQL contracts, full regression/static checks and independent review; Ubuntu PG checks explicitly pending.
- [ ] Only then mark complete local M11 course, sync requested branch/PR; main merge requires explicit user approval.
