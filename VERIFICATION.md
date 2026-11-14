# Verification record

Verified locally on **2026-11-13**, macOS ARM64. The implementation and all
required local checks pass. Live-service acceptance and Docker runtime validation
remain outstanding for the reasons below.

## Provenance and versions

- Language: Python; checkout tested on Python 3.14.4, installed wheel on Python 3.13.16.
- Starter: [Query-farm/vgi-starter-python](https://github.com/Query-farm/vgi-starter-python/tree/9cca9105474d6e0052b8e343c8ea21f3d1a7503c),
  template 1.3.0, revision `9cca9105474d6e0052b8e343c8ea21f3d1a7503c`.
- Runtime SDK: `vgi-python 0.51.1`, transport `vgi-rpc 0.53.1`.
- SQLLogicTest engine: `haybarn-unittest 2.0.0rc11`.
- Separate stock engine: `duckdb 2.0.0`, signed VGI from `query_farm`, extension
  version `e75482339d`, platform `osx_arm64`.
- The original starter passed `make check` before adaptation. Its sample
  functions, unit tests and SQL assertions were retained separately.

## Completed checks

| Command / check | Result |
| --- | --- |
| `make check` | Pass |
| Ruff format/check, strict mypy, pydoclint | Pass |
| `uv run pytest -q` | 91 passed |
| `make test-sql TRANSPORT=stdio` | 140 assertions, 3 files, zero skips |
| `make test-sql TRANSPORT=launch` | 140 assertions, 3 files, zero skips |
| `make test-sql TRANSPORT=http` | 104 assertions, 2 files, zero skips |
| `make lint-explain` | 100/100, zero errors/warnings/info; L2; strict `fail_on = "info"` |
| `make test-duckdb` | 20 stdio + 23 authenticated HTTP assertions passed |
| `make package` | Wheel and source archive built |
| `uvx twine check dist/*` | Both distributions passed |
| Wheel installed into a fresh Python 3.13 environment outside the checkout | 20 stdio + 23 authenticated HTTP assertions passed |
| Source archive extracted outside the checkout, `uv sync --locked --no-dev --no-editable` | 20 stdio + 23 authenticated HTTP assertions passed |
| Shell syntax and `git diff --check` | Pass |

The transport totals include preserved starter regressions; the FRED/ALFRED SQL
file contributes 61 assertions in each transport. All API data in these checks
comes from an offline synthetic fixture. HTTP tests exercise pagination and
DuckDB secret forwarding across batches, including 429 retries.

Authentication follow-up: FRED credentials work through a `fred_api` SQL secret
or `FRED_API_KEY`, with secret precedence. HTTP bearer authentication uses
`VGI_BEARER_TOKENS`. The new checks reject missing/wrong bearer tokens (including
an upstream FRED key used as a worker token), reject malformed/empty configuration
without leaking it, and verify paginated ALFRED queries through authenticated HTTP.
The SQL test proves that a valid worker bearer alone cannot replace the separate
FRED API key. HTTP health checks intentionally remain unauthenticated.

Local evidence is in `build/check.log`, `build/duckdb-verification.json`,
`build/wheel-verification.json`, `build/sdist-verification.json`, and
`build/http-auth-verification.json`. Packaged HTTP results are in
`build/wheel-http-auth-verification.json` and `build/sdist-http-auth-verification.json`. The `build/`
and `dist/` directories are intentionally ignored by Git. Temporary paths in
artifact reports describe environments that were removed after successful tests.

## Not yet verified

- **Live FRED/ALFRED calls:** no `FRED_API_KEY` was configured. After supplying a
  key, run `make smoke-live`. It queries live GDP metadata, a 2020 historical
  snapshot, and multiple revisions of one quarter. The command fails clearly
  if the key is missing; fixtures are never substituted in live mode.
- **Docker runtime:** Docker CLI is installed, but its daemon was unavailable
  (`~/.docker/run/docker.sock` absent). The non-root image and health check are
  configured; CI contains a Docker smoke test.
- GitHub CI, Linux/Windows runners, published releases, hosted deployments and
  optional agent-based L3 metadata evaluation were not run. No remote repository
  was created and no artifacts were published during that initial validation.

## Tested SQL contract

With `FRED_API_KEY` supplied in the launching environment, install/load VGI as
shown in README.md, then:

```sql
ATTACH 'fred' (TYPE vgi,
    LOCATION '/absolute/path/to/vgi-fred/.venv/bin/vgi-fred-worker');

SELECT date, value, value_text
FROM fred.main.observations('GDP', page_size := 1)
ORDER BY date;

SELECT value
FROM fred.main.alfred_observations('GDP', '2020-05-01');

SELECT value, realtime_start, realtime_end
FROM fred.main.alfred_revisions('GDP',
    observation_end := '2020-01-01', page_size := 1)
ORDER BY realtime_start;
```

These statements were verified against fixtures through real SQL engines. Live
values will differ. The complete deterministic expectations are in
`vgi-worker.json`; test fixture values are not actual economic observations.

## Publication recovery — 2026-11-14

The public source repository is https://github.com/Query-farm/vgi-fred. The
existing domain implementation and dependency lock were preserved. The metadata
source/support links now point to this repository, and container publishing is
manual-only. No service was deployed.

`make check` passes: 91 pytest tests; 149 SQL assertions over stdio, 149 over Unix
launch, and 110 over HTTP; Ruff, strict mypy and pydoclint; metadata 100/100, L2,
zero findings with 30 actual local fixture requests; and 20 stock DuckDB 2.0.0
contract checks over each of stdio and authenticated HTTP. SQL test connections
use an empty temporary secret directory and prohibit persistent secrets; fake
SQL credentials are explicitly temporary. The metadata bootstrap configures
Haybarn at connection creation before any secret manager can read saved secrets.

Live FRED calls, Docker execution and optional agent-based checks were not run in
this recovery. Published notebooks use synthetic local HTTP fixtures, clearly
distinguished from official economic observations.
