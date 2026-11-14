"""Deterministic FRED/ALFRED contract fixture; never contacts the real service."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

API_KEY = "0" * 32  # Public, deliberately invalid test credential.
SERIES = [
    {
        "id": "GDP",
        "title": "Fixture GDP",
        "observation_start": "2020-01-01",
        "observation_end": "2020-07-01",
        "frequency": "Quarterly",
        "frequency_short": "Q",
        "units": "Billions of Dollars",
        "units_short": "Bil. of $",
        "seasonal_adjustment": "Seasonally Adjusted Annual Rate",
        "seasonal_adjustment_short": "SAAR",
        "last_updated": "2020-10-29 07:30:00-05",
        "popularity": 99,
        "notes": "Synthetic test data; not economic data.",
        "realtime_start": "2020-01-01",
        "realtime_end": "9999-12-31",
    },
    {"id": "UNRATE", "title": "Fixture Unemployment Rate", "frequency": "Monthly", "popularity": 98},
    {"id": "CPIAUCSL", "title": "Fixture Consumer Price Index", "frequency": "Monthly", "popularity": 97},
]
OBSERVATIONS = [
    {"date": "2020-01-01", "value": "100.0", "realtime_start": "2020-04-30", "realtime_end": "2020-05-27"},
    {"date": "2020-01-01", "value": "101.25", "realtime_start": "2020-05-28", "realtime_end": "2020-06-24"},
    {"date": "2020-01-01", "value": "102.50", "realtime_start": "2020-06-25", "realtime_end": "9999-12-31"},
    {"date": "2020-04-01", "value": ".", "realtime_start": "2020-07-30", "realtime_end": "9999-12-31"},
    {"date": "2020-07-01", "value": "110.00", "realtime_start": "2020-10-29", "realtime_end": "9999-12-31"},
]
CATEGORIES = [
    {"id": 125, "name": "Fixture Trade", "parent_id": 0},
    {"id": 126, "name": "Fixture Growth", "parent_id": 0},
]
RELEASES = [
    {"id": 53, "name": "Fixture GDP Release", "press_release": True},
    {"id": 54, "name": "Fixture Prices", "press_release": False},
]
SOURCES = [{"id": 1, "name": "Fixture Publisher", "link": "https://example.org"}, {"id": 2, "name": "Fixture Bank"}]


class FredServer(ThreadingHTTPServer):
    """Local server with inspectable requests and deterministic error injection."""

    requests: list[tuple[str, dict[str, str]]]
    failures: dict[str, int]

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests = []
        self.failures = {}


class Handler(BaseHTTPRequestHandler):
    """Implement the documented FRED response envelopes and real-time filtering."""

    server: FredServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # Avoid logging query-string credentials.

    def send_json(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        path = url.path.removeprefix("/fred/")
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.server.requests.append((path, query))
        if query.get("api_key") != API_KEY:
            self.send_json(400, {"error_code": 400, "error_message": "Bad API key: " + query.get("api_key", "")})
            return
        identifier = query.get("series_id", "")
        if identifier == "ERROR":
            self.send_json(500, {"error_message": query["api_key"]})
            return
        if identifier == "REDIRECT":
            self.send_json(302, {}, {"Location": "https://example.invalid/stolen?api_key=" + query["api_key"]})
            return
        fingerprint = path + str(sorted(query.items()))
        if os.environ.get("FRED_MOCK_FLAKY") == "1" or identifier == "RETRY":
            seen = self.server.failures.get(fingerprint, 0)
            self.server.failures[fingerprint] = seen + 1
            if seen == 0:
                self.send_json(429, {}, {"Retry-After": "0"})
                return
        first, last = query.get("realtime_start", "1776-07-04"), query.get("realtime_end", "9999-12-31")
        rows: list[Any]
        paginated = True
        if path == "series/observations":
            key = "observations"
            rows = [dict(row) for row in OBSERVATIONS]
            if query.get("output_type") == "4":
                earliest: dict[str, dict[str, Any]] = {}
                for row in rows:
                    earliest.setdefault(row["date"], row)
                rows = list(earliest.values())
            rows = [
                row
                for row in rows
                if row["realtime_start"] <= last
                and row["realtime_end"] >= first
                and query.get("observation_start", "1776-07-04")
                <= row["date"]
                <= query.get("observation_end", "9999-12-31")
            ]
            for row in rows:
                row["realtime_start"] = max(row["realtime_start"], first)
                row["realtime_end"] = min(row["realtime_end"], last)
            if identifier == "EMPTY":
                rows = []
            if identifier == "MALFORMED":
                rows = [{"date": "2020-01-01", "value": "NaN"}]
        elif path == "series/vintagedates":
            key = "vintage_dates"
            rows = sorted({r["realtime_start"] for r in OBSERVATIONS if first <= r["realtime_start"] <= last})
        elif path in {"series", "series/search", "category/series", "release/series"}:
            key = "seriess"  # This spelling is part of the FRED API.
            rows = [dict(r) for r in SERIES]
            if path == "series":
                rows = [r for r in rows if r["id"] == identifier]
                paginated = False
            elif query.get("search_text") == "empty":
                rows = []
        elif path in {"category", "category/children", "series/categories"}:
            key, rows, paginated = "categories", list(CATEGORIES), False
            if path == "category":
                rows = [r for r in rows if r["id"] == int(query["category_id"])]
        elif path == "releases":
            key, rows = "releases", list(RELEASES)
        elif path == "sources":
            key, rows = "sources", list(SOURCES)
        elif path == "release/dates":
            key = "release_dates"
            rows = [
                {"release_id": int(query["release_id"]), "date": d}
                for d in ["2020-04-30", "2020-05-28", "2020-06-25"]
                if first <= d <= last
            ]
        else:
            self.send_json(404, {"error_code": 404})
            return
        if query.get("sort_order") == "desc":
            rows.reverse()
        if paginated:
            offset, limit, total = int(query.get("offset", 0)), int(query.get("limit", 1000)), len(rows)
            body = {key: rows[offset : offset + limit], "count": total, "offset": offset, "limit": limit}
            if identifier == "STALLED":
                body[key] = []
                body["count"] = 10
            if identifier == "CHANGED" and offset:
                body["count"] = total + 1
            if identifier == "WRONG_OFFSET":
                body["offset"] = offset + 1
        else:
            body = {key: rows}
        self.send_json(200, body)


if __name__ == "__main__":
    server = FredServer()
    print(f"PORT:{server.server_port}", flush=True)
    server.serve_forever()
