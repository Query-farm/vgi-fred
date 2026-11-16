"""Typed streaming table functions for FRED discovery and ALFRED history."""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Annotated, Any, ClassVar
from zoneinfo import ZoneInfo

import pyarrow as pa
from vgi.arguments import Arg
from vgi.invocation import BindResponse
from vgi.metadata import FunctionExample
from vgi.table_function import BindParams, OutputCollector, ProcessParams, TableFunctionGenerator, init_single_worker
from vgi_rpc import ArrowSerializableDataclass

from .api import SECRET_TYPE, Config, FredError, get_json, validate_key
from .meta import column, object_tags

OBSERVATIONS = pa.schema(
    [
        column("series_id", pa.string(), "FRED series identifier.", nullable=False),
        column("date", pa.date32(), "Period measured by the observation, not its publication date.", nullable=False),
        column("value", pa.float64(), "Numeric observation; FRED's missing marker '.' becomes SQL NULL."),
        column(
            "value_text", pa.string(), "Original numeric text, preserving decimal precision and the missing marker."
        ),
        column("realtime_start", pa.date32(), "First date this value is valid within the requested real-time window."),
        column("realtime_end", pa.date32(), "Last inclusive validity date; 9999-12-31 denotes an open end."),
    ]
)
SERIES = pa.schema(
    [
        column("id", pa.string(), "FRED series identifier.", nullable=False),
        column("title", pa.string(), "Series title."),
        column("observation_start", pa.date32(), "First observation date."),
        column("observation_end", pa.date32(), "Last observation date."),
        column("frequency", pa.string(), "Native frequency description."),
        column("frequency_short", pa.string(), "Native frequency abbreviation."),
        column("units", pa.string(), "Measurement units."),
        column("units_short", pa.string(), "Measurement units abbreviation."),
        column("seasonal_adjustment", pa.string(), "Seasonal adjustment description."),
        column("seasonal_adjustment_short", pa.string(), "Seasonal adjustment abbreviation."),
        column("last_updated", pa.timestamp("us", tz="UTC"), "Last upstream update, normalized to UTC."),
        column("popularity", pa.int64(), "FRED popularity score."),
        column("group_popularity", pa.int64(), "Group popularity, when supplied by the endpoint."),
        column("notes", pa.string(), "Source notes and attribution; NULL if absent."),
        column("realtime_start", pa.date32(), "First inclusive real-time date for this metadata."),
        column("realtime_end", pa.date32(), "Last inclusive real-time date for this metadata."),
    ]
)
CATEGORIES = pa.schema(
    [
        column("id", pa.int64(), "Category identifier.", nullable=False),
        column("name", pa.string(), "Category name."),
        column("parent_id", pa.int64(), "Parent category; root category has identifier zero."),
        column("notes", pa.string(), "Optional category notes."),
    ]
)
RELEASES = pa.schema(
    [
        column("id", pa.int64(), "Release identifier.", nullable=False),
        column("name", pa.string(), "Release name."),
        column("press_release", pa.bool_(), "Whether the release has a press release."),
        column("link", pa.string(), "Upstream publisher URL."),
        column("notes", pa.string(), "Optional release notes."),
        column("realtime_start", pa.date32(), "First inclusive real-time date."),
        column("realtime_end", pa.date32(), "Last inclusive real-time date."),
    ]
)
SOURCES = pa.schema(
    [
        column("id", pa.int64(), "Source identifier.", nullable=False),
        column("name", pa.string(), "Data publisher name."),
        column("link", pa.string(), "Data publisher URL."),
        column("notes", pa.string(), "Optional source notes."),
        column("realtime_start", pa.date32(), "First inclusive real-time date."),
        column("realtime_end", pa.date32(), "Last inclusive real-time date."),
    ]
)
VINTAGES = pa.schema(
    [
        column("series_id", pa.string(), "FRED series identifier.", nullable=False),
        column("vintage_date", pa.date32(), "Date when observations were added or revised.", nullable=False),
    ]
)
RELEASE_DATES = pa.schema(
    [
        column("release_id", pa.int64(), "Release identifier.", nullable=False),
        column(
            "date", pa.date32(), "Publisher's release date; not necessarily FRED availability time.", nullable=False
        ),
    ]
)

# Composed examples: each combines two of this worker's functions in one query. Each
# is attached to the function that drives it (and so also reaches the schema list).
FIRST_PRINT_REVISION_EXAMPLE = FunctionExample(
    description=(
        "Compare each 2020 GDP quarter's first published value (initial_releases) with today's value "
        "(observations); revision is current minus first print, NULL where FRED reports a missing value"
    ),
    sql=(
        "SELECT date, f.value AS first_value, c.value AS current_value, c.value - f.value AS revision "
        "FROM fred.main.initial_releases('GDP', observation_start := '2020-01-01', "
        "observation_end := '2020-12-31') AS f "
        "JOIN fred.main.observations('GDP', observation_start := '2020-01-01', "
        "observation_end := '2020-12-31') AS c USING (date) ORDER BY date"
    ),
)
VINTAGE_PATH_EXAMPLE = FunctionExample(
    description=(
        "Trace the Q1 2020 GDP value (observation date 2020-01-01) as it stood on each 2020 vintage date: "
        "vintage_dates joined to the alfred_revisions validity interval covering it, from the first release on"
    ),
    sql=(
        "SELECT v.vintage_date, r.value AS q1_2020_value "
        "FROM fred.main.vintage_dates('GDP', realtime_start := '2020-01-01', realtime_end := '2020-12-31') AS v "
        "JOIN fred.main.alfred_revisions('GDP', observation_start := '2020-01-01', "
        "observation_end := '2020-01-01') AS r "
        "ON v.vintage_date BETWEEN r.realtime_start AND r.realtime_end ORDER BY v.vintage_date"
    ),
)
SEARCH_IN_CATEGORY_EXAMPLE = FunctionExample(
    description=(
        "Of the top 100 'unemployment rate' search hits, show the 10 most popular that are filed in FRED's "
        "Unemployment Rate category (32447)"
    ),
    sql=(
        "SELECT s.id, s.title, s.popularity "
        "FROM fred.main.search_series('unemployment rate', max_rows := 100) AS s "
        "WHERE s.id IN (SELECT id FROM fred.main.category_series(32447)) "
        "ORDER BY s.popularity DESC, s.id LIMIT 10"
    ),
)


@dataclass(frozen=True, kw_only=True)
class PageArgs:
    """Shared pagination and metadata-vintage arguments."""

    page_size: Annotated[int, Arg("page_size", default=1000, ge=1, le=1000, doc="Rows per upstream request, 1-1000.")]
    max_rows: Annotated[
        int, Arg("max_rows", default=0, ge=0, le=10000000, doc="Maximum returned rows; 0 streams all matches.")
    ]
    start_offset: Annotated[int, Arg("start_offset", default=0, ge=0, doc="Number of matching upstream rows to skip.")]
    realtime_start: Annotated[
        str,
        Arg("realtime_start", default="", doc="Inclusive real-time start (YYYY-MM-DD); empty pins today in St. Louis."),
    ]
    realtime_end: Annotated[
        str, Arg("realtime_end", default="", doc="Inclusive real-time end (YYYY-MM-DD); empty pins today in St. Louis.")
    ]


@dataclass(frozen=True, kw_only=True)
class SeriesArgs(PageArgs):
    """A series identifier and real-time window."""

    series_id: Annotated[
        str, Arg(0, doc="FRED series identifier, for example GDP or UNRATE.", pattern=r"^[A-Za-z0-9_.-]{1,100}$")
    ]


@dataclass(frozen=True, kw_only=True)
class ObservationArgs(SeriesArgs):
    """Observation window, transformation and optional frequency aggregation."""

    observation_start: Annotated[
        str, Arg("observation_start", default="1776-07-04", doc="First measured period (YYYY-MM-DD), inclusive.")
    ]
    observation_end: Annotated[
        str, Arg("observation_end", default="9999-12-31", doc="Last measured period (YYYY-MM-DD), inclusive.")
    ]
    units: Annotated[
        str,
        Arg(
            "units",
            default="lin",
            choices=["lin", "chg", "ch1", "pch", "pc1", "pca", "cch", "cca", "log"],
            doc="FRED transformation: lin, chg, ch1, pch, pc1, pca, cch, cca, log.",
        ),
    ]
    frequency: Annotated[
        str,
        Arg(
            "frequency",
            default="",
            choices=[
                "",
                "d",
                "w",
                "bw",
                "m",
                "q",
                "sa",
                "a",
                "wef",
                "weth",
                "wew",
                "wetu",
                "wem",
                "wesu",
                "wesa",
                "bwew",
                "bwem",
            ],
            doc="Lower output frequency; empty preserves native frequency. FRED rejects upsampling.",
        ),
    ]
    aggregation_method: Annotated[
        str,
        Arg(
            "aggregation_method",
            default="avg",
            choices=["avg", "sum", "eop"],
            doc="Frequency aggregation: average, sum, or end of period.",
        ),
    ]
    sort_order: Annotated[
        str,
        Arg(
            "sort_order",
            default="asc",
            choices=["asc", "desc"],
            doc="Order by observation date, ascending or descending.",
        ),
    ]


@dataclass(frozen=True, kw_only=True)
class SnapshotArgs(ObservationArgs):
    """Explicit historical date for a point-in-time snapshot."""

    as_of: Annotated[str, Arg(1, doc="Required historical knowledge date, YYYY-MM-DD. Sets both real-time bounds.")]


@dataclass(frozen=True, kw_only=True)
class HistoryArgs(ObservationArgs):
    """All available revisions by default, with optional real-time bounds."""

    realtime_start: Annotated[
        str,
        Arg("realtime_start", default="1776-07-04", doc="First real-time date, inclusive; defaults to all history."),
    ]
    realtime_end: Annotated[
        str, Arg("realtime_end", default="9999-12-31", doc="Last real-time date, inclusive; defaults to open end.")
    ]


@dataclass(frozen=True, kw_only=True)
class VintageArgs(SeriesArgs):
    """Discover dates when a series changed."""

    realtime_start: Annotated[str, Arg("realtime_start", default="1776-07-04", doc="Earliest vintage date, inclusive.")]
    realtime_end: Annotated[str, Arg("realtime_end", default="9999-12-31", doc="Latest vintage date, inclusive.")]
    sort_order: Annotated[str, Arg("sort_order", default="asc", choices=["asc", "desc"], doc="Order of vintage dates.")]


@dataclass(frozen=True, kw_only=True)
class SearchArgs(PageArgs):
    """Search text and upstream series filters."""

    search_text: Annotated[str, Arg(0, doc="Words to search in series metadata.", pattern=r"\S")]
    search_type: Annotated[
        str,
        Arg(
            "search_type",
            default="full_text",
            choices=["full_text", "series_id"],
            doc="Search full metadata or series identifiers.",
        ),
    ]
    order_by: Annotated[
        str,
        Arg(
            "order_by",
            default="search_rank",
            choices=[
                "search_rank",
                "series_id",
                "title",
                "units",
                "frequency",
                "seasonal_adjustment",
                "realtime_start",
                "realtime_end",
                "last_updated",
                "observation_start",
                "observation_end",
                "popularity",
                "group_popularity",
            ],
            doc="Upstream sort field; defaults to search relevance.",
        ),
    ]
    sort_order: Annotated[
        str, Arg("sort_order", default="desc", choices=["asc", "desc"], doc="Upstream sort direction.")
    ]
    tag_names: Annotated[str, Arg("tag_names", default="", doc="Optional semicolon-separated required FRED tags.")]
    exclude_tag_names: Annotated[
        str, Arg("exclude_tag_names", default="", doc="Optional semicolon-separated excluded FRED tags.")
    ]
    filter_variable: Annotated[
        str,
        Arg(
            "filter_variable",
            default="",
            choices=["", "frequency", "units", "seasonal_adjustment"],
            doc="Optional metadata property to filter; requires filter_value.",
        ),
    ]
    filter_value: Annotated[
        str, Arg("filter_value", default="", doc="Value paired with filter_variable, e.g. Monthly.")
    ]


@dataclass(frozen=True, kw_only=True)
class CategoryArgs(PageArgs):
    """A FRED category; identifier zero is the root."""

    category_id: Annotated[int, Arg(0, ge=0, doc="Nonnegative FRED category identifier; 0 is the root.")]


@dataclass(frozen=True, kw_only=True)
class ReleaseArgs(PageArgs):
    """A FRED release identifier."""

    release_id: Annotated[int, Arg(0, ge=1, doc="Positive FRED release identifier.")]


@dataclass(frozen=True, kw_only=True)
class ReleaseDateArgs(ReleaseArgs):
    """Historical or scheduled publisher release dates."""

    realtime_start: Annotated[str, Arg("realtime_start", default="1776-07-04", doc="Earliest release date, inclusive.")]
    realtime_end: Annotated[str, Arg("realtime_end", default="9999-12-31", doc="Latest release date, inclusive.")]
    include_release_dates_with_no_data: Annotated[
        bool,
        Arg(
            "include_release_dates_with_no_data", default=False, doc="Include scheduled release dates with no data yet."
        ),
    ]
    sort_order: Annotated[str, Arg("sort_order", default="asc", choices=["asc", "desc"], doc="Order of release dates.")]


@dataclass(kw_only=True)
class PageState(ArrowSerializableDataclass):
    """Small HTTP-serializable cursor containing no credentials or response data."""

    offset: int
    emitted: int = 0
    total: int = -1
    done: bool = False
    realtime_start: str = ""
    realtime_end: str = ""


def _date(value: str, name: str) -> date:
    """Validate an ISO date within FRED's supported range."""
    try:
        result = date.fromisoformat(value)
        if result.isoformat() != value or result < date(1776, 7, 4):
            raise ValueError
        return result
    except ValueError, TypeError:
        raise FredError(f"{name} must be YYYY-MM-DD between 1776-07-04 and 9999-12-31") from None


def validate_args(args: PageArgs) -> None:
    """Validate date relationships and mutually dependent options before any HTTP call."""
    data = asdict(args)
    for name in ("observation_start", "observation_end", "realtime_start", "realtime_end", "as_of"):
        if data.get(name):
            _date(data[name], name)
    for first, last in (("observation_start", "observation_end"), ("realtime_start", "realtime_end")):
        if data.get(first) and data.get(last) and data[first] > data[last]:
            raise FredError(f"{first} must not be after {last}")
    if isinstance(args, SnapshotArgs):
        _date(args.as_of, "as_of")
        if args.realtime_start or args.realtime_end:
            raise FredError("as_of cannot be combined with realtime_start or realtime_end")
    if isinstance(args, SearchArgs):
        if len(args.search_text) > 1000:
            raise FredError("search_text must not exceed 1000 characters")
        if bool(args.filter_variable) != bool(args.filter_value):
            raise FredError("filter_variable and filter_value must be supplied together")
        if args.exclude_tag_names and not args.tag_names:
            raise FredError("exclude_tag_names requires tag_names")


def _convert(value: Any, field: pa.Field[Any]) -> Any:
    """Convert response values strictly; missing nullable fields become NULL."""
    if value is None:
        if not field.nullable:
            raise FredError(f"FRED response is missing required column {field.name}")
        return None
    try:
        if pa.types.is_date32(field.type):
            if value == "." and field.nullable:
                return None
            return date.fromisoformat(value)
        if pa.types.is_timestamp(field.type):
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                raise ValueError
            return parsed
        if pa.types.is_floating(field.type):
            if value == ".":
                return None
            if isinstance(value, bool):
                raise ValueError
            number = float(value)
            if not math.isfinite(number):
                raise ValueError
            return number
        if pa.types.is_integer(field.type):
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                raise ValueError
            return int(value)
        if pa.types.is_boolean(field.type):
            if not isinstance(value, bool):
                raise ValueError
            return value
        if not isinstance(value, str):
            raise ValueError
        return value
    except ValueError, TypeError, OverflowError:
        raise FredError(f"FRED response has invalid {field.name}") from None


def parse_rows(raw: list[Any], schema: pa.Schema, series_id: str | None = None) -> list[dict[str, Any]]:
    """Map API rows to the declared Arrow schema without silently coercing corrupt values."""
    result = []
    for item in raw:
        if schema.equals(VINTAGES):
            row = {"series_id": series_id, "vintage_date": item}
        elif not isinstance(item, dict):
            raise FredError("FRED response contains a non-object row")
        else:
            row = dict(item)
        if schema.equals(OBSERVATIONS):
            if "value" not in row or row["value"] is None:
                raise FredError("FRED observation is missing value; expected numeric text or '.'")
            row.update(series_id=series_id, value_text=row.get("value"))
        result.append({f.name: _convert(row.get(f.name), f) for f in schema})
    return result


def _count(body: dict[str, Any], name: str) -> int:
    """Require a nonnegative integer pagination field."""
    value = body.get(name)
    if type(value) is not int or value < 0:
        raise FredError(f"FRED response has invalid pagination field {name}")
    return value


def function_meta(
    name: str, title: str, description: str, schema: pa.Schema, sql: str, *composed: FunctionExample
) -> Any:
    """Build consistent, discoverable metadata for an authenticated table function.

    ``composed`` examples combine this function with others and follow its own example.
    """
    types = {
        "string": "VARCHAR",
        "int64": "BIGINT",
        "double": "DOUBLE",
        "date32[day]": "DATE",
        "bool": "BOOLEAN",
        "timestamp[us, tz=UTC]": "TIMESTAMP WITH TIME ZONE",
    }
    docs = (
        f"{description} Returns typed columns {', '.join(schema.names)}. "
        "Authenticate with a fred_api DuckDB secret (api_key) or worker environment FRED_API_KEY. "
        "page_size controls request size; max_rows=0 streams all matches; start_offset skips upstream rows. "
        "Date arguments use YYYY-MM-DD. Empty real-time bounds pin the current St. Louis date for the scan. "
        "NULL arguments are rejected; absent optional response fields and '.' observation values return NULL. "
        "Pages are streamed with bounded retries; upstream errors fail the query. "
        "Dates are daily, not intraday publication timestamps. SQL WHERE filters are local; pass "
        "date bounds to reduce API work."
    )

    class Meta:
        name: str
        description: str
        tags: dict[str, str]
        examples: list[FunctionExample]

    Meta.name = name
    Meta.description = description
    Meta.tags = {
        **object_tags(
            title=title,
            category="api",
            keywords=f"fred, alfred, economics, time series, {name}",
            doc_llm=docs,
            doc_md=f"## {name}\n\n{docs}",
        ),
        "vgi.result_columns_schema": json.dumps(
            [
                {
                    "name": f.name,
                    "type": types[str(f.type)],
                    "description": (f.metadata or {}).get(b"comment", b"").decode(),
                }
                for f in schema
            ]
        ),
    }
    Meta.examples = [FunctionExample(sql=sql, description=title), *composed]
    return Meta


@init_single_worker
class FredFunction[T: PageArgs](TableFunctionGenerator[T, PageState]):
    """One API page per Arrow batch, with validated progress and portable state."""

    FIXED_SCHEMA: ClassVar[pa.Schema]
    ENDPOINT: ClassVar[str]
    ROW_KEY: ClassVar[str]
    PAGINATED: ClassVar[bool] = True
    OUTPUT_TYPE: ClassVar[int] = 1

    @classmethod
    def on_bind(cls, params: BindParams[T]) -> BindResponse:
        """Validate arguments and declare the requested credential and output schema."""
        validate_args(params.args)
        if cls.ENDPOINT == "category" and (params.args.realtime_start or params.args.realtime_end):
            raise FredError("category does not support real-time filters; use category_children or category_series")
        params.secrets.get(SECRET_TYPE)
        return BindResponse(output_schema=cls.FIXED_SCHEMA)

    @classmethod
    def initial_state(cls, params: ProcessParams[T]) -> PageState:
        """Anchor default real-time dates once so pagination cannot cross a date boundary."""
        today = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
        args = params.args
        if isinstance(args, SnapshotArgs):
            first = last = args.as_of
        else:
            first, last = args.realtime_start or today, args.realtime_end or today
        if first > last:
            raise FredError("realtime_start must not be after realtime_end")
        return PageState(offset=args.start_offset, realtime_start=first, realtime_end=last)

    @classmethod
    def process(cls, params: ProcessParams[T], state: PageState, out: OutputCollector) -> None:
        """Fetch, validate and emit a page; never carry a key in continuation state."""
        if state.done:
            out.finish()
            return
        key = None
        secrets = list(params.secrets.of_type(SECRET_TYPE))
        if len(secrets) > 1:
            raise FredError("Multiple fred_api secrets matched; keep a single applicable secret")
        if secrets:
            scalar = secrets[0].get("api_key")
            key = scalar.as_py() if scalar is not None else None
        if key is None:
            key = os.environ.get("FRED_API_KEY")
        key = validate_key(key)
        args = params.args
        limit = min(args.page_size, args.max_rows - state.emitted) if args.max_rows else args.page_size
        query: dict[str, str | int] = {}
        for name, value in asdict(args).items():
            if (
                name not in {"page_size", "max_rows", "start_offset", "as_of", "realtime_start", "realtime_end"}
                and value != ""
            ):
                query[name] = str(value).lower() if isinstance(value, bool) else value
        if cls.ENDPOINT != "category":
            query.update(realtime_start=state.realtime_start, realtime_end=state.realtime_end)
        if cls.PAGINATED:
            query.update(limit=limit, offset=state.offset)
        if cls.FIXED_SCHEMA.equals(OBSERVATIONS):
            query["output_type"] = cls.OUTPUT_TYPE
        body = get_json(cls.ENDPOINT, query, key, Config.from_env())
        raw = body.get(cls.ROW_KEY)
        if not isinstance(raw, list):
            raise FredError(f"FRED response is missing the {cls.ROW_KEY} array")
        if cls.PAGINATED:
            total, offset = _count(body, "count"), _count(body, "offset")
            if offset != state.offset or len(raw) > limit or (raw and offset + len(raw) > total):
                raise FredError("FRED returned inconsistent pagination")
            if state.total != -1 and total != state.total:
                raise FredError("FRED result count changed during pagination; retry with a fixed historical window")
            if not raw and offset < total:
                raise FredError("FRED returned an empty page before the advertised result count")
            state.total = total
            state.offset += len(raw)
            state.done = state.offset >= total
        else:
            raw = raw[state.offset : state.offset + args.max_rows] if args.max_rows else raw[state.offset :]
            state.done = True
        rows = parse_rows(raw, cls.FIXED_SCHEMA, getattr(args, "series_id", None))
        state.emitted += len(rows)
        if args.max_rows and state.emitted >= args.max_rows:
            state.done = True
        try:
            batch = pa.RecordBatch.from_pylist(rows, schema=params.output_schema)
        except pa.ArrowException, OverflowError, ValueError:
            raise FredError("FRED response cannot be represented by the declared output schema") from None
        out.emit(batch)


class Observations(FredFunction[ObservationArgs]):
    """Read current FRED observations with optional transformations and frequency aggregation."""

    FIXED_SCHEMA = OBSERVATIONS
    ENDPOINT = "series/observations"
    ROW_KEY = "observations"
    PAGINATED = True
    OUTPUT_TYPE = 1
    Meta = function_meta(
        "observations",
        "FRED Observations",
        "Read current FRED observations with optional transformations and frequency aggregation.",
        OBSERVATIONS,
        "SELECT date, value FROM fred.main.observations('UNRATE', observation_start := '2020-01-01') ORDER BY date",
    )


class AlfredObservations(FredFunction[SnapshotArgs]):
    """Read observations known on the required as_of date; both real-time bounds equal that date."""

    FIXED_SCHEMA = OBSERVATIONS
    ENDPOINT = "series/observations"
    ROW_KEY = "observations"
    PAGINATED = True
    OUTPUT_TYPE = 1
    Meta = function_meta(
        "alfred_observations",
        "ALFRED Historical Snapshot",
        "Read observations known on the required as_of date; both real-time bounds equal that date.",
        OBSERVATIONS,
        "SELECT date, value FROM fred.main.alfred_observations('GDP', '2020-07-30', "
        "observation_start := '2020-01-01') ORDER BY date",
    )


class AlfredRevisions(FredFunction[HistoryArgs]):
    """Read every value revision and its inclusive real-time validity interval; defaults to all history."""

    FIXED_SCHEMA = OBSERVATIONS
    ENDPOINT = "series/observations"
    ROW_KEY = "observations"
    PAGINATED = True
    OUTPUT_TYPE = 1
    Meta = function_meta(
        "alfred_revisions",
        "ALFRED Revision History",
        "Read every value revision and its inclusive real-time validity interval; defaults to all history.",
        OBSERVATIONS,
        "SELECT date, value, realtime_start, realtime_end FROM fred.main.alfred_revisions('GDP', "
        "observation_start := '2020-01-01', observation_end := '2020-01-01') ORDER BY date, "
        "realtime_start",
    )


class InitialReleases(FredFunction[HistoryArgs]):
    """Read first published values using FRED output_type=4; defaults to the complete real-time history."""

    FIXED_SCHEMA = OBSERVATIONS
    ENDPOINT = "series/observations"
    ROW_KEY = "observations"
    PAGINATED = True
    OUTPUT_TYPE = 4
    Meta = function_meta(
        "initial_releases",
        "ALFRED Initial Releases",
        "Read first published values using FRED output_type=4; defaults to the complete real-time history.",
        OBSERVATIONS,
        "SELECT date, value, realtime_start FROM fred.main.initial_releases('GDP', "
        "observation_start := '2020-01-01') ORDER BY date",
        FIRST_PRINT_REVISION_EXAMPLE,
    )


class VintageDates(FredFunction[VintageArgs]):
    """Discover dates when observations were added or revised; defaults to all available vintage dates."""

    FIXED_SCHEMA = VINTAGES
    ENDPOINT = "series/vintagedates"
    ROW_KEY = "vintage_dates"
    PAGINATED = True
    OUTPUT_TYPE = 1
    Meta = function_meta(
        "vintage_dates",
        "ALFRED Vintage Dates",
        "Discover dates when observations were added or revised; defaults to all available vintage dates.",
        VINTAGES,
        "SELECT vintage_date FROM fred.main.vintage_dates('GDP', realtime_start := '2020-01-01', "
        "realtime_end := '2020-12-31') ORDER BY vintage_date",
        VINTAGE_PATH_EXAMPLE,
    )


class Series(FredFunction[SeriesArgs]):
    """Read units, frequency, source notes and update timestamps for a series, optionally as historically known."""

    FIXED_SCHEMA = SERIES
    ENDPOINT = "series"
    ROW_KEY = "seriess"
    PAGINATED = False
    OUTPUT_TYPE = 1
    Meta = function_meta(
        "series",
        "FRED Series Metadata",
        "Read units, frequency, source notes and update timestamps for a series, optionally as historically known.",
        SERIES,
        "SELECT id, title, units, frequency FROM fred.main.series('GDP') ORDER BY id",
    )


class SearchSeries(FredFunction[SearchArgs]):
    """Search FRED series by text or identifier, optionally restricting tags, metadata and real-time dates."""

    FIXED_SCHEMA = SERIES
    ENDPOINT = "series/search"
    ROW_KEY = "seriess"
    PAGINATED = True
    OUTPUT_TYPE = 1
    Meta = function_meta(
        "search_series",
        "Search FRED Series",
        "Search FRED series by text or identifier, optionally restricting tags, metadata and real-time dates.",
        SERIES,
        "SELECT id, title, popularity FROM fred.main.search_series('unemployment', max_rows := 20) "
        "ORDER BY popularity DESC, id",
        SEARCH_IN_CATEGORY_EXAMPLE,
    )


class Category(FredFunction[CategoryArgs]):
    """Look up one FRED category, including its parent and optional notes."""

    FIXED_SCHEMA = CATEGORIES
    ENDPOINT = "category"
    ROW_KEY = "categories"
    PAGINATED = False
    OUTPUT_TYPE = 1
    Meta = function_meta(
        "category",
        "FRED Category",
        "Look up one FRED category, including its parent and optional notes.",
        CATEGORIES,
        "SELECT id, name, parent_id FROM fred.main.category(125) ORDER BY id",
    )


class CategoryChildren(FredFunction[CategoryArgs]):
    """List immediate children of a FRED category; use category zero to start browsing the hierarchy."""

    FIXED_SCHEMA = CATEGORIES
    ENDPOINT = "category/children"
    ROW_KEY = "categories"
    PAGINATED = False
    OUTPUT_TYPE = 1
    Meta = function_meta(
        "category_children",
        "FRED Category Children",
        "List immediate children of a FRED category; use category zero to start browsing the hierarchy.",
        CATEGORIES,
        "SELECT id, name FROM fred.main.category_children(0) ORDER BY id",
    )


class CategorySeries(FredFunction[CategoryArgs]):
    """Stream series metadata in a FRED category, optionally as known during a historical real-time window."""

    FIXED_SCHEMA = SERIES
    ENDPOINT = "category/series"
    ROW_KEY = "seriess"
    PAGINATED = True
    OUTPUT_TYPE = 1
    Meta = function_meta(
        "category_series",
        "FRED Category Series",
        "Stream series metadata in a FRED category, optionally as known during a historical real-time window.",
        SERIES,
        "SELECT id, title FROM fred.main.category_series(125, max_rows := 100) ORDER BY id",
    )


class Releases(FredFunction[PageArgs]):
    """List economic data releases and publisher links, optionally within a historical real-time window."""

    FIXED_SCHEMA = RELEASES
    ENDPOINT = "releases"
    ROW_KEY = "releases"
    PAGINATED = True
    OUTPUT_TYPE = 1
    Meta = function_meta(
        "releases",
        "FRED Releases",
        "List economic data releases and publisher links, optionally within a historical real-time window.",
        RELEASES,
        "SELECT id, name FROM fred.main.releases() ORDER BY id",
    )


class ReleaseSeries(FredFunction[ReleaseArgs]):
    """Stream the series belonging to an economic release, with units, frequency and source notes."""

    FIXED_SCHEMA = SERIES
    ENDPOINT = "release/series"
    ROW_KEY = "seriess"
    PAGINATED = True
    OUTPUT_TYPE = 1
    Meta = function_meta(
        "release_series",
        "FRED Release Series",
        "Stream the series belonging to an economic release, with units, frequency and source notes.",
        SERIES,
        "SELECT id, title FROM fred.main.release_series(53, max_rows := 100) ORDER BY id",
    )


class ReleaseDates(FredFunction[ReleaseDateArgs]):
    """List publisher release dates; these dates do not guarantee when data became available in FRED."""

    FIXED_SCHEMA = RELEASE_DATES
    ENDPOINT = "release/dates"
    ROW_KEY = "release_dates"
    PAGINATED = True
    OUTPUT_TYPE = 1
    Meta = function_meta(
        "release_dates",
        "FRED Release Calendar",
        "List publisher release dates; these dates do not guarantee when data became available in FRED.",
        RELEASE_DATES,
        "SELECT release_id, date FROM fred.main.release_dates(53, realtime_start := '2020-01-01', "
        "realtime_end := '2020-12-31') ORDER BY date",
    )


class Sources(FredFunction[PageArgs]):
    """List economic data publishers and their links, with optional historical real-time bounds."""

    FIXED_SCHEMA = SOURCES
    ENDPOINT = "sources"
    ROW_KEY = "sources"
    PAGINATED = True
    OUTPUT_TYPE = 1
    Meta = function_meta(
        "sources",
        "FRED Data Sources",
        "List economic data publishers and their links, with optional historical real-time bounds.",
        SOURCES,
        "SELECT id, name, link FROM fred.main.sources() ORDER BY id",
    )


class SeriesCategories(FredFunction[SeriesArgs]):
    """Find the categories containing a series, to navigate to related economic indicators."""

    FIXED_SCHEMA = CATEGORIES
    ENDPOINT = "series/categories"
    ROW_KEY = "categories"
    PAGINATED = False
    OUTPUT_TYPE = 1
    Meta = function_meta(
        "series_categories",
        "FRED Series Categories",
        "Find the categories containing a series, to navigate to related economic indicators.",
        CATEGORIES,
        "SELECT id, name FROM fred.main.series_categories('GDP') ORDER BY id",
    )


FUNCTIONS: list[type[Any]] = [
    Observations,
    AlfredObservations,
    AlfredRevisions,
    InitialReleases,
    VintageDates,
    Series,
    SearchSeries,
    Category,
    CategoryChildren,
    CategorySeries,
    Releases,
    ReleaseSeries,
    ReleaseDates,
    Sources,
    SeriesCategories,
]
