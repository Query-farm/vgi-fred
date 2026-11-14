"""Synthetic GDP response fixture served only on loopback; no official observations."""

from __future__ import annotations

import json
import os
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

FAKE_KEY = "0" * 32
REFERENCE = "2021-03-31"
MANAGED = (
    "FRED_API_KEY",
    "FRED_API_BASE_URL",
    "FRED_ALLOW_INSECURE_HTTP",
    "FRED_MIN_INTERVAL_SECONDS",
    "FRED_TIMEOUT_SECONDS",
    "FRED_MAX_ATTEMPTS",
    "FRED_MOCK_FLAKY",
)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        url = urlsplit(self.path)
        query = {key: value[0] for key, value in parse_qs(url.query).items()}
        if query.pop("api_key", None) != FAKE_KEY:
            self.send_error(400, "Only the public fixture key is accepted")
            return
        path = url.path.removeprefix("/fred/")
        assert query["series_id"] == "GDP"
        assert query["file_type"] == "json"
        self.server.requests.append((path, query))
        if path == "series":
            body = {"seriess": [self.server.fixture["series"]]}
        elif path == "series/observations":
            assert query["units"] == "lin" and query["output_type"] == "1"
            first, last = query["realtime_start"], query["realtime_end"]
            rows = [
                dict(row)
                for row in self.server.fixture["observations"]
                if row["realtime_start"] <= last
                and row["realtime_end"] >= first
                and query["observation_start"] <= row["date"] <= query["observation_end"]
            ]
            for row in rows:
                row["realtime_start"] = max(row["realtime_start"], first)
                row["realtime_end"] = min(row["realtime_end"], last)
            offset, limit = int(query["offset"]), int(query["limit"])
            body = {
                "observations": rows[offset : offset + limit],
                "count": len(rows),
                "offset": offset,
                "limit": limit,
            }
        else:
            self.send_error(404, "This synthetic fixture supports only series and observations")
            return
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class Fixture:
    def __init__(self, as_of):
        try:
            selected = date.fromisoformat(as_of)
        except (ValueError, TypeError) as exc:
            raise ValueError("AS_OF must be an ISO date from 2019-04-30 through 2021-03-31") from exc
        if as_of != selected.isoformat() or not date(2019, 4, 30) <= selected <= date.fromisoformat(
            REFERENCE
        ):
            raise ValueError("AS_OF must be an ISO date from 2019-04-30 through 2021-03-31")
        self.previous = {key: os.environ.get(key) for key in MANAGED}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.fixture = json.loads((Path(__file__).parent / "fixtures/gdp.json").read_text())
        self.server.requests = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        for key in MANAGED:
            os.environ.pop(key, None)
        os.environ.update(
            FRED_API_BASE_URL=f"http://127.0.0.1:{self.server.server_port}/fred",
            FRED_ALLOW_INSECURE_HTTP="1",
            FRED_MIN_INTERVAL_SECONDS="0",
            FRED_TIMEOUT_SECONDS="10",
            FRED_MAX_ATTEMPTS="1",
        )

    def audit(self, as_of):
        """Check each actual HTTP request against the four materialized SQL scans."""
        observations = self.server.fixture["observations"]
        expected = [("series", "2021-03-31", "2021-03-31", None)]
        for first, last in [("1776-07-04", REFERENCE), (as_of, as_of), (REFERENCE, REFERENCE)]:
            count = sum(r["realtime_start"] <= last and r["realtime_end"] >= first for r in observations)
            for offset in range(0, count, 5):
                expected.append(("series/observations", first, last, str(offset)))
        actual = [
            (p, q["realtime_start"], q["realtime_end"], q.get("offset")) for p, q in self.server.requests
        ]
        assert sorted(actual, key=str) == sorted(expected, key=str), "Unexpected or repeated upstream scan"
        assert all(q.get("limit", "5") == "5" for p, q in self.server.requests if p != "series")
        return len(actual)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
