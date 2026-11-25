"""Exercise API semantics, failure handling, streaming, and actual worker transport."""

from __future__ import annotations

import http.client
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pyarrow as pa
import pytest
from vgi.arguments import Arguments
from vgi.client import Client
from vgi.invocation import BindResponse

from fred_worker.api import Config, FredError, get_json, retry_delay, validate_key
from fred_worker.auth import validate_http_auth
from fred_worker.functions import OBSERVATIONS, SERIES, PageState, parse_rows
from test.mock_api.fred_server import API_KEY, FredServer


@pytest.fixture()
def fred_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[FredServer]:
    server = FredServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("FRED_API_KEY", API_KEY)
    monkeypatch.setenv("FRED_API_BASE_URL", f"http://127.0.0.1:{server.server_port}/fred")
    monkeypatch.setenv("FRED_ALLOW_INSECURE_HTTP", "1")
    monkeypatch.setenv("FRED_MIN_INTERVAL_SECONDS", "0")
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def fred_client(fred_api: FredServer) -> Iterator[Client]:
    with Client([sys.executable, "-m", "fred_worker.worker"], pool=None) as client:
        yield client


def table(client: Client, name: str, *positional: Any, secret_key: str | None = None, **named: Any) -> pa.Table:
    bound: list[BindResponse] = []
    batches = list(
        client.table_function(
            bind_result_callback=bound.append,
            function_name=name,
            schema_path=["main"],
            secrets={"fixture": {"type": "fred_api", "api_key": secret_key}},
            arguments=Arguments(
                positional=tuple(pa.scalar(v) for v in positional),
                named={k: pa.scalar(v) for k, v in named.items()},
            ),
        )
    )
    return pa.Table.from_batches(batches, schema=bound[-1].output_schema)


def test_observations_stream_missing_values_and_precision(fred_client: Client, fred_api: FredServer) -> None:
    result = table(fred_client, "observations", "GDP", page_size=1)
    assert result["value"].to_pylist() == [102.5, None, 110.0]
    assert result["value_text"].to_pylist() == ["102.50", ".", "110.00"]
    assert result.schema.field("date").type == pa.date32()
    assert [q["offset"] for _, q in fred_api.requests] == ["0", "1", "2"]
    assert len({q["realtime_start"] for _, q in fred_api.requests}) == 1


def test_alfred_snapshot_excludes_future_revisions_and_observations(fred_client: Client, fred_api: FredServer) -> None:
    before = table(fred_client, "alfred_observations", "GDP", "2020-05-01", page_size=1)
    after = table(fred_client, "alfred_observations", "GDP", "2020-05-28", page_size=1)
    assert before["value"].to_pylist() == [100.0]
    assert after["value"].to_pylist() == [101.25]
    assert before["date"].to_pylist() == [date(2020, 1, 1)]
    assert all(q["realtime_start"] == q["realtime_end"] for _, q in fred_api.requests)


def test_all_revisions_keep_validity_intervals(fred_client: Client) -> None:
    result = table(fred_client, "alfred_revisions", "GDP", observation_end="2020-01-01", page_size=1)
    assert result["value"].to_pylist() == [100.0, 101.25, 102.5]
    assert result["realtime_end"].to_pylist() == [date(2020, 5, 27), date(2020, 6, 24), date(9999, 12, 31)]


def test_initial_releases_and_vintage_discovery(fred_client: Client, fred_api: FredServer) -> None:
    first = table(fred_client, "initial_releases", "GDP", page_size=1)
    assert first["value"].to_pylist() == [100.0, None, 110.0]
    assert all(q["output_type"] == "4" for _, q in fred_api.requests)
    dates = table(fred_client, "vintage_dates", "GDP", page_size=2, realtime_end="2020-06-30")
    assert dates["vintage_date"].to_pylist() == [date(2020, 4, 30), date(2020, 5, 28), date(2020, 6, 25)]


def test_empty_and_explicit_subset(fred_client: Client, fred_api: FredServer) -> None:
    empty = table(fred_client, "observations", "EMPTY")
    assert empty.num_rows == 0
    assert empty.schema.names == OBSERVATIONS.names
    fred_api.requests.clear()
    subset = table(fred_client, "alfred_revisions", "GDP", page_size=2, start_offset=1, max_rows=3)
    assert subset["value"].to_pylist() == [101.25, 102.5, None]
    assert [(q["offset"], q["limit"]) for _, q in fred_api.requests] == [("1", "2"), ("3", "1")]
    assert table(fred_client, "observations", "GDP", start_offset=100).num_rows == 0


@pytest.mark.parametrize(
    ("name", "args", "count"),
    [
        ("series", ("GDP",), 1),
        ("search_series", ("inflation",), 3),
        ("category", (125,), 1),
        ("category_children", (0,), 2),
        ("category_series", (125,), 3),
        ("series_categories", ("GDP",), 2),
        ("releases", (), 2),
        ("release_series", (53,), 3),
        ("release_dates", (53,), 3),
        ("sources", (), 2),
    ],
)
def test_discovery_functions(fred_client: Client, name: str, args: tuple[Any, ...], count: int) -> None:
    assert table(fred_client, name, *args, page_size=1).num_rows == count


def test_timestamp_and_optional_metadata(fred_client: Client) -> None:
    result = table(fred_client, "series", "GDP")
    assert result["last_updated"][0].as_py() == datetime(2020, 10, 29, 12, 30, tzinfo=UTC)
    assert result["group_popularity"][0].as_py() is None


def test_upstream_parameters(fred_client: Client, fred_api: FredServer) -> None:
    table(
        fred_client,
        "observations",
        "GDP",
        observation_start="2020-01-01",
        observation_end="2020-04-01",
        units="pch",
        frequency="a",
        aggregation_method="sum",
        sort_order="desc",
    )
    query = fred_api.requests[-1][1]
    assert {k: query[k] for k in ("units", "frequency", "aggregation_method", "sort_order")} == {
        "units": "pch",
        "frequency": "a",
        "aggregation_method": "sum",
        "sort_order": "desc",
    }
    table(
        fred_client,
        "search_series",
        "a & b",
        filter_variable="frequency",
        filter_value="Monthly",
        tag_names="usa;monthly",
        exclude_tag_names="discontinued",
    )
    assert fred_api.requests[-1][1]["search_text"] == "a & b"
    assert fred_api.requests[-1][1]["tag_names"] == "usa;monthly"


@pytest.mark.parametrize(
    ("name", "args", "kwargs"),
    [
        ("observations", ("GDP",), {"page_size": 0}),
        ("observations", ("GDP",), {"observation_start": "2020-02-30"}),
        ("observations", ("GDP",), {"observation_start": "20210101"}),
        ("observations", ("GDP",), {"observation_start": "2021-01-01", "observation_end": "2020-01-01"}),
        ("observations", ("GDP",), {"units": "watts"}),
        ("observations", ("GDP",), {"max_rows": -1}),
        ("observations", ("GDP",), {"start_offset": -1}),
        ("observations", (None,), {}),
        ("alfred_observations", ("GDP", ""), {}),
        ("alfred_observations", ("GDP", "2020-05-01"), {"realtime_start": "2020-01-01"}),
        ("search_series", (" ",), {}),
        ("search_series", ("inflation",), {"filter_variable": "frequency"}),
        ("search_series", ("inflation",), {"exclude_tag_names": "usa"}),
    ],
)
def test_invalid_arguments_make_no_request(
    fred_client: Client, fred_api: FredServer, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> None:
    with pytest.raises(Exception, match=r".+"):
        table(fred_client, name, *args, **kwargs)
    assert fred_api.requests == []


@pytest.mark.parametrize("identifier", ["STALLED", "CHANGED", "WRONG_OFFSET", "MALFORMED"])
def test_corrupt_or_changing_pages_fail(fred_client: Client, identifier: str) -> None:
    with pytest.raises(Exception, match="FRED"):
        table(fred_client, "alfred_revisions", identifier, page_size=1)


def test_rate_limit_retry(fred_client: Client, fred_api: FredServer) -> None:
    assert table(fred_client, "observations", "RETRY").num_rows == 3
    assert len(fred_api.requests) == 2


def test_redirect_never_followed(fred_api: FredServer) -> None:
    with pytest.raises(FredError, match="HTTP 302") as exc:
        get_json("series/observations", {"series_id": "REDIRECT"}, API_KEY, Config.from_env())
    assert API_KEY not in str(exc.value)
    assert exc.value.__suppress_context__
    assert len(fred_api.requests) == 1


def test_permanent_failure_no_retry_or_secret_leak(fred_api: FredServer) -> None:
    with pytest.raises(FredError, match="HTTP 400") as exc:
        get_json("series", {"series_id": "GDP"}, "a" * 32, Config.from_env())
    assert "a" * 32 not in str(exc.value)
    assert len(fred_api.requests) == 1


def test_server_errors_are_bounded(fred_api: FredServer) -> None:
    config = Config(base_url=Config.from_env().base_url, min_interval=0, attempts=3)
    with patch("fred_worker.api.time.sleep"), pytest.raises(FredError, match="HTTP 500"):
        get_json("series/observations", {"series_id": "ERROR"}, API_KEY, config)
    assert len(fred_api.requests) == 3


def test_retry_after_date_and_malformed_header() -> None:
    when = datetime.now(UTC) + timedelta(seconds=10)
    assert 8 <= retry_delay(format_datetime(when), 0) <= 10
    assert retry_delay("60", 0) == 60
    assert 0.5 <= retry_delay("garbage", 0) <= 0.75
    assert 0.5 <= retry_delay("NaN", 0) <= 0.75


@pytest.mark.parametrize("key", [None, "", "secret", "A" * 32, "a" * 31])
def test_key_validation_does_not_echo_secret(key: str | None) -> None:
    with pytest.raises(FredError) as exc:
        validate_key(key)
    if key and len(key) > 6:
        assert key not in str(exc.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/fred",
        "file:///etc/passwd",
        "https://user:pass@host/fred",
        "https://host/fred?api_key=secret",
        "https://host/fred#frag",
        "https://host:bad/fred",
    ],
)
def test_bad_endpoint_rejected(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv("FRED_API_BASE_URL", url)
    with pytest.raises(FredError):
        Config.from_env()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("FRED_MAX_ATTEMPTS", "1.5"),
        ("FRED_TIMEOUT_SECONDS", "nan"),
        ("FRED_TIMEOUT_SECONDS", "0"),
        ("FRED_MIN_INTERVAL_SECONDS", "-1"),
    ],
)
def test_bad_settings_rejected(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(FredError):
        Config.from_env()


def test_response_size_limit(fred_api: FredServer) -> None:
    config = Config(base_url=Config.from_env().base_url, max_response_bytes=10, min_interval=0)
    with pytest.raises(FredError, match="MAX_RESPONSE_BYTES"):
        get_json("series", {"series_id": "GDP"}, API_KEY, config)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "abc", True, None])
def test_corrupt_values_are_not_silent_nulls(value: Any) -> None:
    with pytest.raises(FredError):
        parse_rows([{"date": "2020-01-01", "value": value}], OBSERVATIONS, "GDP")


def test_required_columns_and_timezone_are_validated() -> None:
    with pytest.raises(FredError, match="id"):
        parse_rows([{"title": "missing id"}], SERIES)
    with pytest.raises(FredError, match="last_updated"):
        parse_rows([{"id": "GDP", "last_updated": "2020-01-01 12:00:00"}], SERIES)


def test_state_roundtrip_has_no_credentials() -> None:
    state = PageState(offset=7, emitted=5, total=10, realtime_start="2020-01-01", realtime_end="2020-01-01")
    encoded = state.serialize_to_bytes()
    assert API_KEY.encode() not in encoded
    assert PageState.deserialize_from_bytes(encoded) == state


def test_missing_key_worker_error_is_actionable(fred_api: FredServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRED_API_KEY")
    with (
        Client([sys.executable, "-m", "fred_worker.worker"], pool=None) as client,
        pytest.raises(Exception, match="FRED_API_KEY"),
    ):
        table(client, "observations", "GDP")
    assert fred_api.requests == []


def test_worker_stdout_is_protocol_only(fred_api: FredServer) -> None:
    # A subprocess client decodes Arrow; any banner on stdout would fail this call.
    with Client([sys.executable, "-m", "fred_worker.worker"], pool=None) as client:
        assert table(client, "sources").num_rows == 2


def test_secret_precedes_environment(fred_api: FredServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "a" * 32)
    with Client([sys.executable, "-m", "fred_worker.worker"], pool=None) as client:
        assert table(client, "observations", "GDP", secret_key=API_KEY, page_size=1).num_rows == 3
    assert all(query["api_key"] == API_KEY for _, query in fred_api.requests)


def test_category_sends_only_supported_parameters(fred_client: Client, fred_api: FredServer) -> None:
    table(fred_client, "category", 125)
    assert set(fred_api.requests[-1][1]) == {"category_id", "api_key", "file_type"}
    with pytest.raises(Exception, match="does not support real-time"):
        table(fred_client, "category", 125, realtime_start="2020-01-01")


def test_retry_after_exceeding_budget_fails_without_early_retry(fred_api: FredServer) -> None:
    with patch("fred_worker.api.retry_delay", return_value=300), pytest.raises(FredError, match="time budget"):
        get_json("series/observations", {"series_id": "RETRY"}, API_KEY, Config.from_env())
    assert len(fred_api.requests) == 1


@pytest.mark.parametrize("error", [http.client.BadStatusLine(API_KEY), TimeoutError(API_KEY)])
def test_network_exception_text_is_redacted(error: Exception) -> None:
    with patch("fred_worker.api.urllib.request.build_opener") as factory:
        factory.return_value.open.side_effect = error
        with pytest.raises(FredError, match="network request failed") as exc:
            get_json("series", {"series_id": "GDP"}, API_KEY, Config(attempts=1, min_interval=0))
        assert API_KEY not in str(exc.value)
        assert exc.value.__suppress_context__


WORKER_TOKEN = "fixture-only-worker-token-with-32-characters"


@pytest.fixture()
def fred_http(fred_api: FredServer, tmp_path: Path) -> Iterator[str]:
    port_file = tmp_path / "port"
    environment = os.environ.copy()
    environment.update(VGI_BEARER_TOKENS=f"{WORKER_TOKEN}=analyst", VGI_PROXY_PROOF_MODE="off")
    environment.pop("VGI_JWT_ISSUER", None)
    with (tmp_path / "server.log").open("w+") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "fred_worker.worker", "--http", "--port", "0", "--port-file", str(port_file)],
            stdout=log,
            stderr=log,
            env=environment,
        )
        try:
            deadline = time.monotonic() + 20
            # The port file can appear (or be empty) before the server accepts connections,
            # so wait for a non-empty port and a successful TCP connect.
            while True:
                assert process.poll() is None, "HTTP worker exited before publishing its port"
                if time.monotonic() >= deadline:
                    pytest.fail("HTTP worker did not start within 20 seconds")
                port = port_file.read_text().strip() if port_file.exists() else ""
                if port:
                    try:
                        socket.create_connection(("127.0.0.1", int(port)), timeout=1).close()
                        break
                    except OSError:
                        pass
                time.sleep(0.05)
            yield "http://127.0.0.1:" + port
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.seek(0)
            messages = log.read()
            assert WORKER_TOKEN not in messages
            assert API_KEY not in messages


@pytest.mark.parametrize("token", [None, "incorrect-token", API_KEY])
def test_http_rpc_rejects_missing_or_wrong_bearer(fred_http: str, fred_api: FredServer, token: str | None) -> None:
    headers = {"Authorization": "Bearer " + token} if token else {}
    request = urllib.request.Request(fred_http + "/__describe__", headers=headers)
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=5)
    assert exc.value.code == 401
    exc.value.close()
    with (
        pytest.raises(Exception, match="authentication rejected"),
        Client(transport="http", base_url=fred_http, bearer_token=token, pool=None) as client,
    ):
        table(client, "observations", "GDP")
    assert fred_api.requests == []


def test_http_bearer_authenticated_pages_and_secrets(fred_http: str, fred_api: FredServer) -> None:
    with Client(transport="http", base_url=fred_http, bearer_token=WORKER_TOKEN, pool=None) as client:
        result = table(client, "alfred_revisions", "GDP", page_size=1, secret_key=API_KEY)
    assert result.num_rows == 5
    assert len(fred_api.requests) == 5
    assert all(query["api_key"] == API_KEY for _, query in fred_api.requests)
    assert all(WORKER_TOKEN not in str(query) for _, query in fred_api.requests)


def test_http_health_remains_available_without_bearer(fred_http: str) -> None:
    with urllib.request.urlopen(fred_http + "/health", timeout=5) as response:
        assert response.status == 200


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        ",",
        WORKER_TOKEN,
        "=analyst",
        f"{WORKER_TOKEN}=",
        "short=analyst",
        f"{WORKER_TOKEN}=analyst,",
        f"{WORKER_TOKEN}=analyst,{WORKER_TOKEN}=other",
    ],
)
def test_bad_bearer_configuration_fails_without_exposing_token(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("VGI_BEARER_TOKENS", value)
    with pytest.raises(ValueError, match="Invalid VGI_BEARER_TOKENS") as exc:
        validate_http_auth()
    assert WORKER_TOKEN not in str(exc.value)


def test_bearer_configuration_accepts_multiple_principals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VGI_BEARER_TOKENS", f"{WORKER_TOKEN}=analyst,{WORKER_TOKEN}-second=service")
    validate_http_auth()
    monkeypatch.delenv("VGI_BEARER_TOKENS")
    validate_http_auth()


def test_bad_bearer_configuration_stops_http_startup(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["VGI_BEARER_TOKENS"] = WORKER_TOKEN
    port_file = tmp_path / "port"
    result = subprocess.run(
        [sys.executable, "-m", "fred_worker.worker", "--http", "--port-file", str(port_file)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
    )
    assert result.returncode != 0
    assert "Invalid VGI_BEARER_TOKENS" in result.stderr
    assert WORKER_TOKEN not in result.stderr + result.stdout
    assert not port_file.exists()
