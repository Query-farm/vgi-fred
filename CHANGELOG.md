# Changelog

## 0.1.2 (unreleased)

- Docs link to FRED and ALFRED: the catalog documentation now ends with a
  "Sources and references" section linking the upstream service, its API
  documentation and where to get an API key (VGI183).
- The `main` schema comment is now a short one-line summary, so the schema's
  `vgi.doc_md` adds detail instead of repeating it (VGI102).
- The catalog and `main` schema docs now give an overview (data source, the
  series id as the central key, the current versus real-time ALFRED views, and
  caveats) instead of naming each table function (VGI173). The schema docs are
  now distinct from the catalog docs; per-function guidance stays on each
  function's own description.

## 0.1.1 (unreleased)

- The catalog comment (the one-line description directories show, VGI001) is now
  a short plain-text sentence of at most 300 characters saying what data the
  worker covers. The long-form Markdown documentation remains in the catalog's
  `vgi.doc_md` tag and agent guidance in `vgi.doc_llm`.

## 0.1.0 (2026-11-14)

First release. FRED and ALFRED economic data as typed SQL tables: current
observations, historical vintages, revision intervals, first releases, vintage
dates, and series/category/release/source discovery. Composed examples join
first prints to current values, trace a quarter across vintages, and filter search
hits by category. Source-only release (no PyPI).

Targets: vgi-python 0.51 · VGI protocol 3.3 · Haybarn 2.0.0rc11 · vgi-lint-check 2.x (unpinned)
