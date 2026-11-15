"""Discovery metadata: the one file to edit when you rename or extend the worker.

DuckDB, coding agents and the ``vgi-lint`` metadata checker all read these
tags. Keep every object documented and ``make lint`` stays at 100.

* Catalog-level tags (title, license, author, links, long description) live in
  ``CATALOG_TAGS`` below.
* Every function and table carries ``object_tags(...)``: a title, a category
  from ``SCHEMA_CATEGORIES``, search keywords, and LLM- and human-facing docs.
"""

from __future__ import annotations

import json
from typing import Any

import pyarrow as pa

CATALOG_NAME = "fred"

SOURCE_URL = "https://github.com/Query-farm/vgi-fred"

# Categories group functions in docs and agent tools. Every object's
# ``vgi.category`` must name one of these.
SCHEMA_CATEGORIES = [
    {
        "name": "api",
        "title": "Economic Data",
        "description": "FRED observations, ALFRED history and discovery metadata.",
    },
]


def keywords_array(keywords: str) -> str:
    """Turn ``"a, b, c"`` into the JSON array string ``vgi.keywords`` requires."""
    return json.dumps([k.strip() for k in keywords.split(",") if k.strip()])


def object_tags(*, title: str, category: str, keywords: str, doc_llm: str, doc_md: str) -> dict[str, str]:
    """Build the standard per-object discovery tags."""
    if category not in {c["name"] for c in SCHEMA_CATEGORIES}:
        raise ValueError(f"unknown category {category!r}; add it to SCHEMA_CATEGORIES")
    return {
        "vgi.title": title,
        "vgi.category": category,
        "vgi.keywords": keywords_array(keywords),
        "vgi.doc_llm": doc_llm,
        "vgi.doc_md": doc_md,
    }


def column(name: str, type_: pa.DataType, comment: str, *, nullable: bool = True) -> pa.Field[Any]:
    """Build an Arrow field whose ``comment`` shows up in DuckDB's ``DESCRIBE`` and agent tools."""
    return pa.field(name, type_, nullable=nullable, metadata={b"comment": comment.encode()})


# The catalog comment is the worker's one-line directory description (VGI001):
# plain text, one paragraph, at most 300 characters. Long-form docs live in
# vgi.doc_md and agent guidance in vgi.doc_llm.
CATALOG_COMMENT = (
    "St. Louis Fed FRED and ALFRED economic time series as SQL tables, including "
    "current observations, historical vintages and revisions, and series, category, "
    "release and source discovery. Needs a free FRED API key."
)
CATALOG_DOC_LLM = (
    "FRED and ALFRED economic data through authenticated, typed, streaming SQL table functions. "
    "Use observations for current data; alfred_observations(series_id, as_of) for a historical snapshot; "
    "alfred_revisions for every revision interval; initial_releases for first published values; "
    "vintage_dates for publication/revision dates. Discover series through search_series, series, "
    "category, category_children, category_series, series_categories, releases, release_series, "
    "release_dates and sources. Supply a fred_api secret or FRED_API_KEY. Dates are daily, not intraday. "
    "Missing observation markers become NULL; value_text retains the original representation. "
    "Use named date bounds to reduce upstream work and max_rows for an explicit result cap."
)
CATALOG_DOC_MD = (
    "# vgi-fred\n\n"
    + CATALOG_DOC_LLM
    + "\n\nQueries stream one API page at a time. Credentials stay out of continuation state. "
    "Read README.md for operational limits and examples."
)
SCHEMA_DOC_LLM = CATALOG_DOC_LLM
SCHEMA_DOC_MD = (
    "## main\n\n"
    + CATALOG_DOC_LLM
    + "\n\nStart with search_series and series, then query observations or an explicit historical ALFRED snapshot."
)
AGENT_TEST_TASKS: list[dict[str, str]] = []
CATALOG_TAGS = {
    "vgi.title": "FRED and ALFRED",
    "vgi.executable_examples": json.dumps(
        [
            {
                "name": "discover_alfred",
                "description": "Discover the historical snapshot function without credentials or API requests",
                "sql": (
                    "SELECT function_name FROM duckdb_functions() WHERE database_name = 'fred' "
                    "AND function_name = 'alfred_observations' ORDER BY function_name"
                ),
                "expected_result": [{"function_name": "alfred_observations"}],
            }
        ]
    ),
    "vgi.subtitle": "Economic observations, historical vintages, revision intervals and discovery metadata.",
    "vgi.doc_llm": CATALOG_DOC_LLM,
    "vgi.doc_md": CATALOG_DOC_MD,
    "vgi.source_url": SOURCE_URL,
    "vgi.icon_url": "https://query.farm/media-kit/products/vgi/vgi-logo.png",
    "vgi.license": "0BSD",
    "vgi.author": "vgi-fred contributors",
    "vgi.copyright": "Copyright 2026 vgi-fred contributors",
    "vgi.keywords": keywords_array("fred, alfred, economic data, vintage, revisions, time series, duckdb"),
    "vgi.support_contact": "https://github.com/Query-farm/vgi-fred/issues",
    "vgi.support_policy_url": SOURCE_URL + "#provenance-and-support",
}
