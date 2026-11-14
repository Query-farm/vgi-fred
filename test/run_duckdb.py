"""Verify the worker through stock DuckDB, with offline fixtures or optional live FRED."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORKER_TOKEN = "fixture-only-worker-token-with-32-characters"


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, default=ROOT / ".venv/bin/vgi-fred-worker")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--http-auth", action="store_true", help="Exercise HTTP bearer auth and FRED SQL secrets")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.live and args.http_auth:
        parser.error("--http-auth uses offline fixtures and cannot be combined with --live")
    if args.live and not os.environ.get("FRED_API_KEY"):
        parser.error("Live checks require FRED_API_KEY in the environment")
    server: Any = None
    thread: threading.Thread | None = None
    worker: subprocess.Popen[bytes] | None = None
    if not args.live:
        from test.mock_api.fred_server import API_KEY, FredServer

        server = FredServer()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        os.environ.update(
            FRED_API_KEY=API_KEY,
            FRED_API_BASE_URL=f"http://127.0.0.1:{server.server_port}/fred",
            FRED_ALLOW_INSECURE_HTTP="1",
            FRED_MIN_INTERVAL_SECONDS="0",
        )
    elif os.environ.get("FRED_API_BASE_URL", "https://api.stlouisfed.org/fred") != "https://api.stlouisfed.org/fred":
        parser.error("Live smoke tests require the official FRED API base URL")
    key = os.environ.get("FRED_API_KEY", "")
    report: dict[str, Any] = {"status": "failed", "mode": "live" if args.live else "fixture", "checks": []}
    try:
        duckdb = importlib.import_module("duckdb")
        report["engine"] = "stock DuckDB"
        report["engine_version"] = duckdb.__version__
        if duckdb.__version__ != "2.0.0":
            raise RuntimeError("This verification contract requires duckdb==2.0.0")
        with tempfile.TemporaryDirectory(prefix="vgi-fred-repositories-") as directory:
            con = duckdb.connect(
                ":memory:",
                config={
                    "extension_repository_directory": directory,
                    "secret_directory": str(Path(directory) / "secrets"),
                    "allow_persistent_secrets": False,
                },
            )
            assert con.execute("SELECT name FROM duckdb_secrets()").fetchall() == []
            try:
                con.execute("SET allow_extension_repositories='allowed'")
                con.execute("CREATE EXTENSION REPOSITORY query_farm WITH PREFIX 'https://duckdb-extensions.query.farm'")
                con.execute("INSTALL vgi FROM query_farm; LOAD vgi FROM query_farm")
                report["extension_version"] = con.execute(
                    "SELECT extension_version FROM duckdb_extensions() WHERE extension_name='vgi'"
                ).fetchone()[0]
                report["platform"] = con.execute("PRAGMA platform").fetchone()[0]
                argv = [str(args.worker.resolve())]
                command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
                attach = f"ATTACH 'fred' (TYPE vgi, LOCATION {literal(command)})"
                if args.http_auth:
                    report["mode"] = "fixture-http-auth"
                    port_file = Path(directory) / "worker-port"
                    environment = os.environ.copy()
                    environment.update(VGI_BEARER_TOKENS=f"{WORKER_TOKEN}=sql-test", VGI_PROXY_PROOF_MODE="off")
                    environment.pop("FRED_API_KEY", None)
                    environment.pop("VGI_JWT_ISSUER", None)
                    worker = subprocess.Popen(
                        [*argv, "--http", "--port", "0", "--port-file", str(port_file)],
                        env=environment,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    deadline = time.monotonic() + 20
                    while not port_file.exists():
                        if worker.poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError("Authenticated HTTP worker failed to start")
                        time.sleep(0.05)
                    location = "http://127.0.0.1:" + port_file.read_text().strip()
                    con.execute("INSTALL httpfs FROM core; LOAD httpfs")
                    for label, option in (("missing bearer", ""), ("wrong bearer", ", bearer_token 'wrong-token'")):
                        try:
                            con.execute(f"ATTACH 'fred' (TYPE vgi, LOCATION {literal(location)}{option})")
                        except Exception as exc:
                            if "401" not in str(exc) and "authentication" not in str(exc).lower():
                                raise
                            report["checks"].append({"name": label + " rejected", "status": "passed"})
                        else:
                            raise AssertionError(label + " was accepted")
                    attach = (
                        f"ATTACH 'fred' (TYPE vgi, LOCATION {literal(location)}, bearer_token {literal(WORKER_TOKEN)})"
                    )
                report["attach_sql"] = attach.replace(WORKER_TOKEN, "<redacted>")
                con.execute(attach)
                if args.http_auth:
                    try:
                        con.execute("SELECT count(*) FROM fred.main.observations('GDP')")
                    except Exception as exc:
                        if "FRED_API_KEY" not in str(exc):
                            raise
                        report["checks"].append({"name": "worker bearer does not replace FRED key", "status": "passed"})
                    else:
                        raise AssertionError("FRED query was accepted without its separate API key")
                    con.execute(f"CREATE TEMPORARY SECRET fred_fixture (TYPE fred_api, API_KEY {literal(key)})")
                    assert con.execute("SELECT persistent, storage FROM duckdb_secrets()").fetchall() == [
                        (False, "memory")
                    ]
                if args.live:
                    checks = [
                        {
                            "name": "live series metadata",
                            "sql": "SELECT id FROM fred.main.series('GDP')",
                            "rows": [["GDP"]],
                        },
                        {
                            "name": "live historical snapshot",
                            "sql": (
                                "SELECT count(*) > 0 FROM fred.main.alfred_observations('GDP', '2020-05-01', "
                                "observation_start := '2020-01-01', observation_end := '2020-01-01')"
                            ),
                            "rows": [[True]],
                        },
                        {
                            "name": "live revision history",
                            "sql": (
                                "SELECT count(*) > 1 FROM fred.main.alfred_revisions('GDP', "
                                "observation_start := '2020-01-01', observation_end := '2020-01-01')"
                            ),
                            "rows": [[True]],
                        },
                    ]
                else:
                    checks = json.loads((ROOT / "vgi-worker.json").read_text())["checks"]
                for check in checks:
                    actual = json.loads(json.dumps(con.execute(check["sql"]).fetchall(), default=str))
                    if actual != check["rows"]:
                        raise AssertionError(f"{check['name']}: expected {check['rows']!r}, got {actual!r}")
                    report["checks"].append({"name": check["name"], "status": "passed"})
                con.execute("DETACH fred")
                report["status"] = "passed"
            finally:
                con.close()
    except Exception as exc:
        message = str(exc)
        report["error"] = (message.replace(key, "<redacted>") if key else message).replace(WORKER_TOKEN, "<redacted>")
    finally:
        if worker:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
        if server:
            server.shutdown()
            server.server_close()
        if thread:
            thread.join(timeout=5)
    output = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output)
    print(output, end="")
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
