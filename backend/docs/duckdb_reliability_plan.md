# DuckDB Connector Reliability Plan

Last updated: 2026-02-11

## Goal

Make DuckDB the first production-grade connector path with low LOC and durable behavior:

1. Attach an existing DuckDB file to a worldline.
2. Query attached tables from `run_sql` across separate requests.
3. Keep behavior deterministic enough to support future ontology-lite work.

## Constraints

- Keep changes small and explicit (no framework additions).
- Favor a few reusable helpers over endpoint-specific patches.
- Keep generated demo data out of git history (script-generated, not binary fixture-committed).

## Workstream A: Session-Scoped Attach Fix

Problem:

- DuckDB `ATTACH` is connection-session scoped.
- We currently open fresh DuckDB connections per request, so attached aliases disappear.

Plan:

1. Add a shared worldline connection helper that re-attaches external sources from `_external_sources` whenever a new connection is opened.
2. Use that helper in SQL execution + seed-data schema/list operations.
3. Make detach robust across requests by removing metadata even if alias is not currently attached in-process.

Acceptance:

- `attach-duckdb -> run_sql -> run_sql` works across multiple requests.
- `list attached databases` returns table names for attached aliases.
- `detach` removes alias and later queries to `alias.table` fail as expected.

## Workstream B: Deterministic Finance Demo Source

Plan:

1. Add an on-demand script to generate a secondary DuckDB file with seeded financial data.
2. Keep schema stable for ontology-lite:
   - date, region, segment, product
   - customers, revenue, cogs, opex, refunds, marketing_spend
   - derived gross-profit view
3. Default output to a runtime data path that is gitignored.

Acceptance:

- Script is deterministic by seed.
- Generated DB can be attached via existing `attach-duckdb` endpoint.
- Basic analysis queries (group by month/region/segment) run through `run_sql`.

## Workstream C: Guardrail Tests

Add/extend integration tests for:

1. attach -> query -> query (fresh requests)
2. attach -> list tables includes external table names
3. detach -> query fails

## Out of Scope (for this phase)

- Postgres/MySQL connector execution path
- Connector secrets management and backend connector registry
- Ontology compiler changes (only data-shape prep in this phase)
