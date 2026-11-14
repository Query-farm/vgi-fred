# FRED and ALFRED notebooks

Read saved outputs on GitHub or use **Open in Colab** at the top of either notebook.

| Notebook | Question | Default → experiment |
|---|---|---|
| [01 · Reconstruct GDP vintages](01_reconstruct_gdp_vintages.ipynb) | What could an analyst have known at a historical date? | AS_OF 2020-05-01 → 2020-08-01 |
| [02 · Search rank and comparability](02_search_rank_is_not_comparability.ipynb) | Which search hits pass category and measurement checks? | MIN_POPULARITY 80 → 40 |

Every observation, title, category and popularity score is **authored synthetic data**, not recorded FRED responses. Familiar symbols select fixture routes; synthetic results do not describe the US economy. The real released worker retrieves FRED-shaped responses from an ephemeral loopback service. No real key, official API call or account write is used.

## Run from this directory

Install [uv](https://docs.astral.sh/uv/). Initial packages, hash-verified companions, VGI extension and public Git worker need internet. The shared base supports Python 3.11–3.14 kernels; uv provisions the worker's separate Python 3.13+ environment. No source checkout, local worker build or registered global kernel is needed.

```sh
uv sync --locked --extra runner
uv run --locked --extra runner vgi-notebooks
uv run --locked --extra runner vgi-notebooks 01_reconstruct_gdp_vintages.ipynb --parameters '{"AS_OF":"2020-08-01"}' --output-dir validation/later-vintage
uv run --locked --extra runner vgi-notebooks 02_search_rank_is_not_comparability.ipynb --parameters '{"MIN_POPULARITY":40}' --output-dir validation/lower-threshold
```

Interactive Jupyter uses this environment's kernel and this directory as cwd. Restart and Run All after changing a parameter. Colab setup installs the shared base only, preserving compatible preloaded data/plotting and kernel libraries; `[runner]` is for local publication. Shared tools provide canonical branding, SVG charts, isolated SQL sessions, companion verification, coverage validation and atomic execution. Overrides require a separate output directory; failed runs preserve the last successful notebook.

Both notebooks show the short released launcher:

```sql
INSTALL vgi FROM community;
LOAD vgi;
ATTACH 'fred' (TYPE vgi,
 LOCATION 'uvx --from git+https://github.com/Query-farm/vgi-fred@v0.1.0 vgi-fred-worker');
```

Release v0.1.0 and reviewed current public source both resolve to `752ec0198a7c14eb7797bfa71e6c5958c41b2cd4`. No transitive overrides are needed. The notebook lock fixes publication packages; the worker release owns its declared dependency ranges in a separate uv environment. Upgrade deliberately and rerun both parameter variants.

## Provenance and interpretation

Notebook 01 retains the existing eight-quarter GDP vintage fixture and analytical SQL. Levels are toy billions of US dollars at a seasonally adjusted annual rate. Observation dates identify measured quarters; inclusive real-time intervals identify when versions were known. The fixed reference is 2021-03-31, not today's truth. Snapshot bounds are clipped to their requested date. Missing levels remain SQL NULL; growth compares adjacent calendar quarters and never bridges the deliberately missing 2020 Q2. Growth is quarter-to-quarter percent, not annualized. Advancing AS_OF changes available information; it does not change the reference or complete revision history.

Notebook 02 has six synthetic search hits, five category members and two definition matches. A seed's category membership is checked against the selected category and the root's immediate children. Search rank is then separated from category, quarterly frequency, nominal-dollar units and annual-rate adjustment. The highest-ranked hit is outside the category; other hits fail units, frequency or adjustment. Lowering popularity 80 → 40 adds GDI to GDP, without changing retrieval or definition checks. GDP and GDI describe distinct accounting concepts even when their metadata fields match: the result is a review shortlist, not evidence that series can be added, substituted or compared causally. The fixture does not simulate official search relevance or full catalog coverage.

All API-backed reads are visibly materialized once before local displays. Notebook 01 retains twelve SQL invariants and an exact paginated-request audit. Notebook 02 adds seven SQL checks plus exactly nine local GETs across five functions. Audits store endpoint and noncredential parameters only. Generated audits, review PNGs, experiment copies and logs are ignored; saved notebooks retain native SVG plus GitHub-compatible linked SVG assets.

## Credentials and optional real-service use

Shared `connect(globals())` creates an empty temporary SQL-secret directory before ATTACH, disables persistence and default INI connection autoload, and checks an empty inventory. Helpers temporarily clear inherited FRED key/endpoint settings, route the worker to `127.0.0.1`, and restore the previous environment on cleanup. Each notebook explicitly creates only the public all-zero **TEMPORARY** fake `fred_api` key, checks memory-only storage, drops it, verifies an empty inventory, closes the SQL session and stops the local server. Saved user secrets are never loaded or altered. The fake key is not valid for the real service.

There is no live mode in these notebooks. For official usage, follow the [worker README](../README.md) and [FRED key documentation](https://fred.stlouisfed.org/docs/api/api_key.html): intentionally provide your own key to a separate isolated session using the supported `fred_api` secret or worker `FRED_API_KEY` environment mechanism, and use the default HTTPS endpoint. Enter real credentials outside saved notebook cells/outputs. Remove fixture startup and fake-secret creation in that separate workflow; never silently redirect these teaching fixtures to a real account. Official responses, availability, search ranking, rate limits and live credential behavior were not validated here.

## Coverage and validation

The actual release exposes **15 named functions** and no tables/views. These two focused stories meaningfully cover **8/15**, meeting the required eight for a catalog larger than ten: series, alfred_observations, alfred_revisions, category, category_children, series_categories, category_series and search_series. All authenticated functions remain in the denominator. The seven explicit gaps are observations, initial_releases, vintage_dates, releases, release_series, release_dates and sources; this is not full API coverage.

`coverage.json` includes complete native function/table/view exports, immutable release/current source evidence, successfully executed SQL/result references, editorial findings and receipts hashed from the final saved JSON. Current-interface coverage is distinguished from exact release-runtime evidence even though their source revisions match.

Every default and meaningful experiment is executed. One fresh preloaded Python 3.12 notebook-only run tests the common launch and companion-download path; actual hosted Colab remains untested. Both parameter variants' figures are inspected, and published SVGs are checked in GitHub's actual renderer.

The unchanged release's green [source CI 43619679319](https://github.com/Query-farm/vgi-fred/actions/runs/43619679319) is reused. This notebook-only change does not rebuild the worker, repeat source suites, dispatch deployment workflows or publish containers. Focused fixture checks exercised invalid fake-key rejection, real released SQL pagination, temporary-secret cleanup and idempotent new-helper cleanup.

See the centrally maintained [shared standard](https://github.com/Query-farm/vgi-example-notebooks/blob/main/docs/standard.md) and [coverage format](https://github.com/Query-farm/vgi-example-notebooks/blob/main/docs/coverage.md).

Shared tools are pinned to [v0.3.28](https://github.com/Query-farm/vgi-example-notebooks/releases/tag/v0.3.28). The `fred` registry verifies four companions at public revision `8d209cf45fc436cd55361cc72165d5e4296304aa`: both local server helpers and both synthetic JSON datasets.
