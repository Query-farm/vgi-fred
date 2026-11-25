# Changelog

## 0.2.0 (2026-11-25)

- Speaks VGI protocol 3.4: require vgi-python `>=0.55.2,<0.56`; uv.lock re-locked. This release
  needs Haybarn 2.0.1 (or stock DuckDB 2.0 with `INSTALL vgi FROM query_farm`): Haybarn 2.0.0
  loads a 3.3 `vgi` extension that cannot attach it.
- SQL tests run on haybarn-unittest 2.0.1rc3.
- `serve` extra is `vgi-python[http,oauth]`; the Docker image installs it, so the image runs in
  Orchard hosted mode (`VGI_ORCHARD_HOSTED=1` with VGI_JWT_ISSUER, VGI_JWT_AUDIENCE,
  VGI_JWT_JWKS_URI and VGI_SIGNING_KEY). The HEALTHCHECK uses `HEAD /health`, which stays
  anonymous in hosted mode.

## 0.1.3 (2026-11-16)

- Require vgi-python `>=0.52.2,<0.53`; uv.lock re-locked.
- Move to Python 3.14: `requires-python >=3.14`, CI, the Docker image and lint tooling target 3.14.
- The HTTP User-Agent reports the real version (`vgi-fred/0.1.3`).

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
- The credential-free executable example now binds `sources()` with `LIMIT 0`
  instead of querying `duckdb_functions()`, so it uses the worker (VGI517).

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
