"""Read-only, synthetic FRED discovery endpoints on an ephemeral loopback port."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fixture_api import FAKE_KEY, MANAGED


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
        fixture = self.server.fixture
        if query.get("file_type") != "json":
            self.send_error(400, "JSON format required")
            return
        key, paginated = "categories", False
        if path == "category/children" and query.get("category_id") == "0":
            rows = fixture["categories"]
        elif (
            path == "category"
            and query.get("category_id") == "125"
            or path == "series/categories"
            and query.get("series_id") == "GDP"
        ):
            rows = fixture["categories"][:1]
        elif path == "category/series" and query.get("category_id") == "125":
            key, paginated = "seriess", True
            ids = fixture["category_series"]["125"]
            rows = [r for r in fixture["series"] if r["id"] in ids]
        elif path == "series/search" and query.get("search_text") == "output":
            key, paginated = "seriess", True
            if query.get("order_by") != "popularity" or query.get("sort_order") != "desc":
                self.send_error(400, "Fixture search expects descending popularity")
                return
            rows = sorted(fixture["series"], key=lambda r: (-r["popularity"], r["id"]))
        else:
            self.send_error(404, "Unsupported synthetic discovery route")
            return
        if paginated:
            offset, limit = int(query["offset"]), int(query["limit"])
            body = {key: rows[offset : offset + limit], "count": len(rows), "offset": offset, "limit": limit}
        else:
            body = {key: rows}
        self.server.requests.append((path, query))
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class DiscoveryFixture:
    def __init__(self, minimum_popularity):
        if type(minimum_popularity) is not int or not 0 <= minimum_popularity <= 100:
            raise ValueError("MIN_POPULARITY must be an integer from 0 through 100")
        self.previous = {key: os.environ.get(key) for key in MANAGED}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.fixture = json.loads((Path(__file__).parent / "fixtures/discovery.json").read_text())
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
        self.stopped = False

    def audit(self):
        expected = [(p, None) for p in ["category/children", "category", "series/categories"]]
        expected += [(p, str(offset)) for p in ["category/series", "series/search"] for offset in [0, 2, 4]]
        actual = [(p, q.get("offset")) for p, q in self.server.requests]
        assert sorted(actual, key=str) == sorted(expected, key=str), "Unexpected or repeated fixture request"
        assert all(q.get("limit", "2") == "2" for _, q in self.server.requests)
        assert all(
            q["realtime_start"] == q["realtime_end"] == "2021-03-31"
            for p, q in self.server.requests
            if p != "category"
        )
        return len(actual)

    def stop(self):
        if self.stopped:
            return
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.stopped = True
