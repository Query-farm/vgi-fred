# FRED / ALFRED notebooks

Open [01_reconstruct_gdp_vintages.ipynb](01_reconstruct_gdp_vintages.ipynb) to compare
what an analyst knew on one date with a fixed later GDP vintage. The notebook
shows level revisions, information availability and quarter-to-quarter growth.
Saved outputs use AS_OF=2020-05-01; the focused experiment advances it to 2020-08-01.

## Run from this directory

Install [uv](https://docs.astral.sh/uv/) and Python 3.13 or 3.14. From a standalone
copy of this entire notebooks directory:

```sh
uv sync --locked
uv run python execute.py
```

The runner launches this environment's kernel without changing registered kernels.
For an interactive editor, select `.venv/bin/python`, or explicitly register it:

```sh
uv run python -m ipykernel install --user --name vgi-fred-notebooks --display-name "VGI FRED notebooks"
```

Haybarn is pinned to 2.0.0rc11. The visible literal ATTACH installs the real worker
through uvx from public Git revision
`d0626c1b522a06d39494e7bc1076211e4793b53b`. It does not read the source checkout or
its virtual environment. Internet access is required for packages, VGI installation
and the first Git fetch. Later data retrieval uses the loopback fixture only.
To upgrade, change the visible full revision and rerun the default, experiment,
SQL invariants and request audit; do not assume a newer SDK or worker is compatible.

## Data and credentials

`fixtures/gdp.json` contains explicitly authored synthetic data, not recorded or
official FRED responses. Eight quarters have toy levels in billions of US dollars,
seasonally adjusted annual rate; 2020 Q2 is deliberately missing. The symbol GDP
only selects this fixture route. `fixture_api.py` serves the documented response
shapes locally and audits every paginated request. The worker performs parsing,
secret resolution and streaming; visible SQL performs all economic calculations.

The fixture clears inherited FRED key/endpoint settings, creates an empty temporary
Haybarn secret directory with persistent secrets disabled, verifies an empty
inventory, and installs only a temporary public all-zero fake key. Cleanup drops
the fake secret, verifies an empty inventory, closes the engine and server,
removes the temporary directory and restores the previous environment. No saved
user credentials are read, altered or deleted. There is no live mode here.
Use the worker's root README for separately configured official API usage.

Knowledge dates and observation bounds are inclusive. The reference date remains
2021-03-31. Snapshot bounds are clipped to their as-of date; the independent
revision-history scan preserves release intervals. Missing levels are SQL NULL,
not zero. Growth is quarter-to-quarter percent, not an annualized rate, and never
bridges a missing quarter. Synthetic results do not describe the US economy.

## Focused experiment

```sh
uv run python execute.py --parameters '{"AS_OF":"2020-08-01"}' --output-dir artifacts/later-vintage
```

Compare availability counts, 2020 Q1's level and growth revision, and both figures.
The later information set incorporates Q1 revisions and adds a reported missing Q2.
The raw revision history and reference snapshot stay fixed. Reset AS_OF to
2020-05-01 for the default. To prove portability, copy this directory without its
`.venv`, caches or artifacts into a temporary folder, sync its lock and run the
same experiment there.

The runner rejects overrides without a separate output directory, rejects an
output directory that would overwrite the source, and writes only after complete
success. Experiment copies include assets, fixtures, helpers, environment files
and this README. Invalid dates fail before starting the fixture. Generated
experiments, extracted chart images, logs and environment caches are ignored.

## Validation

Both parameter runs must pass 12 displayed SQL invariants plus an exact local HTTP
request audit. Inspect both embedded figures in each run. Notebook lint:

```sh
uvx ruff check .
uvx ruff format --check .
```

The source publication passed its separate `make check`: 91 pytest tests, SQL
transports, stock DuckDB contracts, formatting, strict typing, documentation and
metadata 100/100 L2 with no findings. Wheel contracts also passed in an isolated
environment. Source and notebook dependencies remain separate. Live FRED calls,
Docker runtime and hosted deployments were not exercised.
