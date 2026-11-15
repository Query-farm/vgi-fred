# vgi-fred

A Python VGI worker exposing **FRED and ALFRED economic data as typed SQL tables**.
Query current observations, historical snapshots, revision intervals, first
releases, vintage dates, and discovery metadata from DuckDB or Haybarn.

The worker uses the official [FRED API v1](https://fred.stlouisfed.org/docs/api/fred/),
which also serves [ALFRED](https://fred.stlouisfed.org/docs/api/fred/alfred.html).
It streams pages as Arrow batches and supports stdio, Unix sockets, and HTTP.

## Install and attach

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```sh
cd /path/to/vgi-fred
make build
```

Set `FRED_API_KEY` in the environment **before starting the SQL engine**. The worker
inherits it when launched. For HTTP, set it in the worker server's environment.
Obtain your own key from the [St. Louis Fed](https://fred.stlouisfed.org/docs/api/api_key.html).
A `.env` file is not loaded automatically.

For stock DuckDB 2.0.0:

```sql
SET allow_extension_repositories = 'allowed';
CREATE EXTENSION REPOSITORY IF NOT EXISTS query_farm
    WITH PREFIX 'https://duckdb-extensions.query.farm';
INSTALL vgi FROM query_farm;
LOAD vgi FROM query_farm;

ATTACH 'fred' (TYPE vgi,
    LOCATION '/absolute/path/to/vgi-fred/.venv/bin/vgi-fred-worker');

SELECT date, value
FROM fred.main.observations('UNRATE', observation_start := '2020-01-01')
ORDER BY date;
```

For Haybarn 2.0.0rc11 use `INSTALL vgi FROM community; LOAD vgi;`, then the same
`ATTACH` and queries. An absolute worker path avoids dependence on the engine's
working directory. For a pooled local worker use `LOCATION 'launch:/absolute/path/to/vgi-fred/.venv/bin/vgi-fred-worker'`.

To run a tagged release without cloning, set
`LOCATION 'uvx --from git+https://github.com/Query-farm/vgi-fred@v0.1.1 vgi-fred-worker'`
(change `@v0.1.1` to move to a newer release).

Alternatively, after attaching, create a session secret of type `fred_api` with an
`API_KEY` field. A matching secret takes precedence over the worker environment:

```sql
-- Replace the placeholder locally; do not commit a real credential.
CREATE TEMPORARY SECRET fred_credentials (TYPE fred_api, API_KEY '<your-fred-api-key>');
```

## Authenticate clients of the HTTP worker

FRED API keys authenticate the worker **to FRED/ALFRED**. HTTP bearer tokens
separately authenticate clients **to this worker**. Both are supported.

Set `VGI_BEARER_TOKENS` to comma-separated `token=principal` pairs before starting
HTTP mode. Use distinct random tokens (for example, 32 random bytes rendered as
64 hexadecimal characters), provided by your deployment's secret manager:

```sh
# Replace the placeholder locally. This token is not your FRED API key.
export VGI_BEARER_TOKENS='<random-worker-token-at-least-32-characters>=analyst'
.venv/bin/vgi-fred-worker --http --host 127.0.0.1 --port 8000
```

The SDK validates `Authorization: Bearer <token>` using constant-time comparisons.
Missing or incorrect tokens receive HTTP 401 before any FRED request. A configured
empty or malformed token list stops startup, with an error that does not echo its
contents. Tokens must be unique, 32–4096 characters, and contain only ASCII letters,
digits, `.`, `_`, `~`, `+`, `/`, or `-`. Principals must be nonempty and contain no
whitespace. `/health` remains unauthenticated for health probes.

Connect from SQL using VGI's built-in bearer option:

```sql
ATTACH 'fred' (TYPE vgi,
    LOCATION 'http://127.0.0.1:8000',
    bearer_token '<your-worker-bearer-token>');

-- Supply upstream credentials separately if the server has no FRED_API_KEY.
CREATE TEMPORARY SECRET fred_credentials (TYPE fred_api, API_KEY '<your-fred-api-key>');
SELECT date, value
FROM fred.main.alfred_observations('GDP', '2020-05-01')
ORDER BY date;
```

Use HTTPS at the deployment boundary. Omit `VGI_BEARER_TOKENS` entirely for a local
unauthenticated HTTP service; bearer configuration applies only to HTTP, not stdio
or Unix sockets. All configured principals may query the full catalog. Restart the
worker after changing tokens. Supplying an HTTP token never substitutes for a FRED
key, and the worker does not forward its HTTP bearer token to FRED.

All functions are under `fred.main`. Discover arguments and column types with
`DESCRIBE SELECT * FROM fred.main.observations('GDP');`. Discovery does not fetch
upstream data and works without a key.

## FRED and ALFRED queries

```sql
-- What was known on this date, excluding later revisions?
SELECT date, value
FROM fred.main.alfred_observations('GDP', '2020-05-01',
    observation_start := '2020-01-01')
ORDER BY date;

-- Every revision of one measured quarter, and when each value was valid.
SELECT date, value, realtime_start, realtime_end
FROM fred.main.alfred_revisions('GDP',
    observation_start := '2020-01-01', observation_end := '2020-01-01')
ORDER BY date, realtime_start;

-- First published values instead of today's revised history.
SELECT date, value, realtime_start
FROM fred.main.initial_releases('GDP', observation_start := '2020-01-01')
ORDER BY date;

-- Combine functions: how far has each 2020 quarter moved since its first print?
SELECT date, f.value AS first_value, c.value AS current_value,
       c.value - f.value AS revision
FROM fred.main.initial_releases('GDP',
    observation_start := '2020-01-01', observation_end := '2020-12-31') AS f
JOIN fred.main.observations('GDP',
    observation_start := '2020-01-01', observation_end := '2020-12-31') AS c
    USING (date)
ORDER BY date;

-- Dates on which this series gained or revised observations.
SELECT vintage_date
FROM fred.main.vintage_dates('GDP',
    realtime_start := '2020-01-01', realtime_end := '2020-12-31')
ORDER BY vintage_date;

-- Discover series, then inspect units and source notes.
SELECT id, title, popularity
FROM fred.main.search_series('unemployment', max_rows := 20)
ORDER BY popularity DESC, id;

SELECT id, title, units, frequency, notes FROM fred.main.series('GDP');

-- Request frequency aggregation upstream.
SELECT date, value
FROM fred.main.observations('UNRATE', frequency := 'a',
    aggregation_method := 'avg', observation_start := '2020-01-01')
ORDER BY date;

-- Persist a chosen vintage with its knowledge date for reproducible analysis.
CREATE TABLE gdp_vintage AS
SELECT DATE '2020-05-01' AS as_of, *
FROM fred.main.alfred_observations('GDP', '2020-05-01');
```

| Function | Purpose |
| --- | --- |
| `observations(series_id)` | Current observations; optionally specify real-time bounds |
| `alfred_observations(series_id, as_of)` | Snapshot known on one explicit historical date |
| `alfred_revisions(series_id)` | All revisions with inclusive validity intervals |
| `initial_releases(series_id)` | First published observations using API output type 4 |
| `vintage_dates(series_id)` | Dates when observations were added or revised |
| `series(series_id)` | Units, frequency, update timestamp, popularity, source notes |
| `search_series(search_text)` | Keyword/identifier search with tag and metadata filters |
| `category(category_id)` | Category name and parent; `0` is the root |
| `category_children(category_id)` | Immediate child categories |
| `category_series(category_id)` | Series belonging to a category |
| `series_categories(series_id)` | Categories containing a series |
| `releases()` | Economic release metadata |
| `release_series(release_id)` | Series belonging to a release |
| `release_dates(release_id)` | Historical/scheduled publisher release dates |
| `sources()` | Data publisher metadata |

Named pagination arguments are `page_size` (1–1000, default 1000), `start_offset`
(default 0), and `max_rows` (default 0, meaning all matching rows). `max_rows` is an
explicit subset request. Nonpaginated endpoints apply offset/cap locally;
`page_size` has no effect on those endpoints.

Observation functions accept `observation_start`, `observation_end`, `units`,
`frequency`, `aggregation_method`, and `sort_order`. Supported transformations are
`lin`, `chg`, `ch1`, `pch`, `pc1`, `pca`, `cch`, `cca`, and `log`. Frequency is empty
(native) or a FRED code such as `m`, `q`, or `a`; upsampling is rejected by FRED.
Aggregation is `avg`, `sum`, or `eop`.

Search also accepts `search_type` (`full_text` or `series_id`), `order_by`,
`sort_order`, semicolon-separated `tag_names` / `exclude_tag_names`, and paired
`filter_variable` / `filter_value`. Release dates support
`include_release_dates_with_no_data` (default false).

## Time and NULL semantics

- `date` is the **period measured**, not the publication date. `realtime_start`
  and `realtime_end` describe knowledge/validity dates and have inclusive bounds.
  They can be clipped to the requested real-time window. `9999-12-31` is retained
  as an open end. See [real-time periods](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html).
- `alfred_observations` sets both real-time bounds to `as_of`. Combining it with
  explicit real-time bounds fails before any API request. To compare multiple
  vintages, join or union explicit snapshot calls.
- Revisions, initial releases, vintage dates, and release dates default to the
  full available real-time range. Other functions pin empty real-time bounds to
  today's date in `America/Chicago` once per scan. `category` has no historical
  filters and rejects explicit real-time bounds.
- Arguments are non-NULL and dates must be ISO `YYYY-MM-DD` in FRED's range
  `1776-07-04` through `9999-12-31`. Invalid ranges fail before fetching data.
- Observations return `DATE`, `DOUBLE`, original `value_text`, and real-time
  dates. FRED's `.` marker becomes SQL `NULL`; `value_text` preserves the exact
  source text for decimal-sensitive workflows. Unexpected nonnumeric/nonfinite
  values fail the query. Missing optional metadata fields become `NULL`.
- `last_updated` is a timezone-aware timestamp normalized to UTC.
- ALFRED is daily historical knowledge, not an intraday publication feed.
  Publisher release dates do not guarantee availability time in FRED. No
  cross-series or cross-request transactional snapshot is promised.

## Operating the worker

Credentials are read on each page and never placed in continuation state. The
worker sends the API key only as required by FRED's query parameter contract;
it does not print URLs, upstream bodies, or credential values in HTTP errors.
Redirects are rejected. HTTPS certificate verification remains enabled.

Transient network failures, HTTP 429, and HTTP 5xx retry with jittered exponential
backoff. Both numeric and HTTP-date `Retry-After` headers are respected; if the
requested delay exceeds the remaining retry budget, the query fails instead of
retrying early. Other HTTP errors fail immediately with their status code.

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `FRED_API_KEY` | unset | Credential fallback when no usable secret is supplied |
| `FRED_TIMEOUT_SECONDS` | `30` | Timeout for blocking network operations, 0.1–120 seconds |
| `FRED_MAX_ATTEMPTS` | `4` | Total attempts per page, integer 1–6 |
| `FRED_RETRY_BUDGET_SECONDS` | `120` | Budget checked between requests/reads and before backoff, 1–600 seconds |
| `FRED_MIN_INTERVAL_SECONDS` | `0.55` | Minimum request-start spacing per process, 0–60 seconds |
| `FRED_MAX_RESPONSE_BYTES` | `16777216` | Maximum JSON page size, 1 KiB–64 MiB |
| `FRED_API_BASE_URL` | `https://api.stlouisfed.org/fred` | Trusted operator-controlled API base, including `/fred` |
| `FRED_ALLOW_INSECURE_HTTP` | unset | `1` permits HTTP only on literal loopback hosts for fixtures |

The limiter coordinates threads in one worker process. Multiple worker processes
or replicas sharing a key need an aggregate limiter at the deployment boundary.
FRED documents [120 requests per minute](https://fred.stlouisfed.org/docs/api/fred/errors.html).
Keep the default spacing unless coordinating that limit elsewhere.

One page is held in memory at a time, with a byte cap. Pagination checks offsets,
counts, and forward progress; an inconsistent or changing count fails instead of
silently truncating. A SQL `WHERE` is evaluated locally: pass observation or
real-time bounds as function arguments to reduce upstream work. There is no
persistent result cache. A cancelled scan stops requesting further pages; an
in-flight network operation remains bounded by its timeout. An individual
blocking read can extend past the between-operation retry-budget check.

For a reproducible runtime install from the checkout or extracted source archive:

```sh
uv sync --locked --no-dev --no-editable
.venv/bin/vgi-fred-worker --http --host 127.0.0.1 --port 8000
```

HTTP provides `/health` through the SDK. A shared HTTP deployment exposes the
server's API quota to its callers; configure `VGI_BEARER_TOKENS` and TLS at your
reverse proxy. Prefer per-client VGI secrets when clients require separate credentials.
The default local launch is stdio. Diagnostics go to stderr; use
`--log-format json` for structured SDK logs.

A non-root Docker image and health check are supplied. Its install uses `uv.lock`:

```sh
docker build -t vgi-fred:local .
docker run --rm -p 127.0.0.1:8000:8000 -e FRED_API_KEY vgi-fred:local
# Or stdio:
docker run --rm -i -e FRED_API_KEY vgi-fred:local stdio
```

## Tests and artifacts

```sh
make check          # formatting, static checks, tests, metadata, both SQL engines
make test-unit      # deterministic API/error/transport tests
make test-sql-all   # Haybarn SQLLogicTest: stdio, launch, HTTP
make test-duckdb    # stock DuckDB 2.0.0, including authenticated HTTP SQL
make smoke-live     # official FRED and ALFRED; requires FRED_API_KEY
make package        # wheel and source archive in dist/
```

Fixtures are synthetic and require no account. They test historical boundaries,
revisions, missing values, pagination, retries, redirects, credential precedence,
invalid input and malformed upstream data. The SQL harness injects rate limits
and verifies session secrets across multiple HTTP batches. `vgi-worker.json`
contains 20 portable, deterministic SQL assertions; `test/run_duckdb.py` starts
the fixture and writes `build/duckdb-verification.json`. The same contract also
runs over authenticated HTTP, with missing/wrong bearer checks and a separate SQL
secret for FRED; its report is `build/http-auth-verification.json`.

Install a wheel with `uv pip install /path/to/dist/vgi_fred_worker-0.1.1-py3-none-any.whl`.
For release validation, run `test/run_duckdb.py --worker /path/to/isolated-env/bin/vgi-fred-worker`
using a Python environment containing `duckdb==2.0.0`. The isolated environment
must contain the built wheel, not an editable checkout. Repeat with `--http-auth`
to check the installed worker with HTTP bearer authentication and FRED SQL secrets.

CI covers Python 3.13/3.14, the starter's SQL transport matrix, stock DuckDB,
metadata (failing even on informational findings) and Docker health. Image publishing requires an explicit manual workflow run. The source is available at [Query-farm/vgi-fred](https://github.com/Query-farm/vgi-fred). Image publishing is manual-only; no hosted service is deployed.

Scope is API v1 series and discovery endpoints listed above. FRED v2 bulk release
downloads, Maps APIs and wide output types 2/3 are not exposed. Historical
snapshots and revision intervals use the stable long-form representation.
Data availability, upstream limits and source-specific rights remain those of
FRED and its publishers; preserve returned source notes when distributing data.

## Provenance and support

Created from `Query-farm/vgi-starter-python`, template 1.3.0, commit
`9cca9105474d6e0052b8e343c8ea21f3d1a7503c`. The original sample functions and tests
remain under `examples/starter_samples/`. The production catalog registers only
FRED/ALFRED functions. SDK constraints remain `vgi-python>=0.51.1,<0.52`; the lock
resolves `vgi-python 0.51.1` and `vgi-rpc 0.53.1`.

Report reproducible issues in [GitHub Issues](https://github.com/Query-farm/vgi-fred/issues). No support SLA is offered. See
[VERIFICATION.md](VERIFICATION.md) for measured checks and unverified deployment
steps. Worker code is licensed under [0BSD](LICENSE); this does not license
third-party economic data. Template update review: `make template-diff`.

## Executed notebook

[Reconstruct GDP vintages](notebooks/README.md) compares historical knowledge dates,
revision intervals and quarter-to-quarter growth using explicit synthetic local
fixtures through the published worker. The notebook runs independently of this checkout.

The [series discovery notebook](notebooks/02_search_rank_is_not_comparability.ipynb) separates synthetic search ranking from category and measurement compatibility. See the [notebook index](notebooks/README.md) for both executed stories and protected experiments.
