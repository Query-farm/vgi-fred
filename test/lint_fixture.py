"""Run the unchanged published metadata checker against isolated local FRED fixtures."""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    fixture = importlib.import_module("test.mock_api.fred_server")
    haybarn = importlib.import_module("haybarn")
    server = fixture.FredServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    os.environ.update(
        FRED_API_KEY=fixture.API_KEY,
        FRED_API_BASE_URL=f"http://127.0.0.1:{server.server_port}/fred",
        FRED_ALLOW_INSECURE_HTTP="1",
        FRED_MIN_INTERVAL_SECONDS="0",
    )
    original_connect = haybarn.connect
    try:
        with tempfile.TemporaryDirectory(prefix="fred-lint-secrets-") as directory:

            def isolated_connect(*args: Any, **kwargs: Any) -> Any:
                config = dict(kwargs.pop("config", {}) or {})
                config.update(secret_directory=directory, allow_persistent_secrets=False)
                connection = original_connect(*args, config=config, **kwargs)
                assert connection.execute("SELECT name FROM duckdb_secrets()").fetchall() == []
                return connection

            haybarn.connect = isolated_connect
            importlib.import_module("vgi_lint_check.cli").app()
    finally:
        haybarn.connect = original_connect
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        print(f"Metadata fixture handled {len(server.requests)} local requests; SQL secrets isolated.", file=sys.stderr)


if __name__ == "__main__":
    main()
