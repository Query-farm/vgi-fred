"""Published example metadata stays consistent with the private agent graders."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fred_worker import functions
from fred_worker.meta import CATALOG_TAGS
from fred_worker.worker import FredWorker

COMPOSED = {
    "initial_releases": functions.FIRST_PRINT_REVISION_EXAMPLE,
    "vintage_dates": functions.VINTAGE_PATH_EXAMPLE,
    "search_series": functions.SEARCH_IN_CATEGORY_EXAMPLE,
}


def test_composed_examples_reach_function_and_schema_lists() -> None:
    by_name = {fn.Meta.name: fn for fn in functions.FUNCTIONS}
    schema_examples = json.loads(FredWorker.catalog.schemas[0].tags["vgi.example_queries"])
    for name, example in COMPOSED.items():
        entry = {"description": example.description, "sql": example.sql}
        # The agent simulator shows only the first five examples of each function.
        assert entry in json.loads(by_name[name].Meta.tags["vgi.example_queries"])[:5]
        assert schema_examples.count(entry) == 1
        assert len(set(re.findall(r"fred\.main\.(\w+)\(", example.sql))) >= 2


def test_every_agent_task_has_a_grader() -> None:
    published = {task["name"] for task in json.loads(CATALOG_TAGS["vgi.agent_test_tasks"])}
    yaml_text = (Path(__file__).resolve().parents[1] / "vgi-agent-tests.yaml").read_text()
    graded = set(re.findall(r"^  - name: (\w+)$", yaml_text, re.MULTILINE))
    assert published == graded
