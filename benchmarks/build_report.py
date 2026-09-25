"""Build human-readable benchmark history from saved run results."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results.json"
CASES = ROOT / "cases.json"
HISTORY = ROOT / "PERFORMANCE.md"
REPORTS = ROOT / "reports"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _cell(value: object) -> str:
    return str(value).replace("|", r"\|").replace("\n", " ")


def _seconds(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    minutes, seconds = divmod(float(value), 60)
    if minutes >= 1:
        return f"{int(minutes)}m {seconds:.1f}s"
    return f"{seconds:.1f}s"


def _tokens(value: object) -> str:
    return f"{value:,}" if isinstance(value, int) else "—"


def _cost(value: object) -> str:
    return f"${value:.4f}" if isinstance(value, (int, float)) else "—"


def _filename(run_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", run_id) + ".md"


def _case_totals(run: dict) -> tuple[float | None, int | None]:
    cases = run["cases"]
    durations = [case.get("duration_seconds") for case in cases]
    token_counts = [case.get("usage", {}).get("total_tokens") for case in cases]
    duration = sum(durations) if all(isinstance(x, (int, float)) for x in durations) else None
    tokens = sum(token_counts) if all(isinstance(x, int) for x in token_counts) else None
    return duration, tokens


def _utc_timestamp(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError(f"Expected a UTC timestamp: {value}")
    return timestamp


def _validate(runs: list[dict], case_ids: list[str]) -> None:
    run_ids = [run["run_id"] for run in runs]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("Duplicate run_id in results.json")
    filenames = [_filename(run_id) for run_id in run_ids]
    if len(filenames) != len(set(filenames)):
        raise ValueError("Run IDs collide as report filenames")
    for run in runs:
        if not run.get("model"):
            raise ValueError(f"Missing model for {run['run_id']}")
        if not run.get("reasoning_level"):
            raise ValueError(f"Missing reasoning_level for {run['run_id']}")
        run_start = _utc_timestamp(run["started_at"])
        run_finish = _utc_timestamp(run["finished_at"])
        if run_finish <= run_start:
            raise ValueError(f"Invalid run interval in {run['run_id']}")
        if abs((run_finish - run_start).total_seconds() - run["duration_seconds"]) > 0.1:
            raise ValueError(f"Run duration does not match timestamps: {run['run_id']}")
        found = [case["case_id"] for case in run["cases"]]
        if found != case_ids:
            raise ValueError(f"Expected benchmark cases once, in order, in {run['run_id']}")
        previous_finish = run_start
        for case in run["cases"]:
            case_id = case["case_id"]
            start = _utc_timestamp(case["started_at"])
            finish = _utc_timestamp(case["finished_at"])
            if start < previous_finish or finish <= start or finish > run_finish:
                raise ValueError(f"Overlapping or invalid case interval: {case_id}")
            previous_finish = finish
            duration = case.get("duration_seconds")
            if not isinstance(duration, (int, float)) or duration <= 0:
                raise ValueError(f"Missing case duration: {case_id}")
            if abs((finish - start).total_seconds() - duration) > 0.1:
                raise ValueError(f"Case duration does not match timestamps: {case_id}")
            timing = case.get("tool_timing_seconds") or {}
            for tool in ("search_catalog", "get_metadata", "retrieve"):
                seconds = timing.get(tool)
                if not isinstance(seconds, (int, float)) or seconds < 0:
                    raise ValueError(f"Missing {tool} timing: {case_id}")
            usage = case.get("usage") or {}
            for key in ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens"):
                value = usage.get(key)
                if not isinstance(value, int) or value < 0:
                    raise ValueError(f"Missing measured {key}: {case_id}")
            if usage["cached_input_tokens"] > usage["input_tokens"]:
                raise ValueError(f"Cached input exceeds input tokens: {case_id}")
            if usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]:
                raise ValueError(f"Token total mismatch: {case_id}")
            if usage["total_tokens"] == 0:
                raise ValueError(f"No measured tokens: {case_id}")
            if not isinstance(case.get("correct"), bool):
                raise ValueError(f"Missing yes/no correctness: {case_id}")
        suite_usage = run.get("usage") or {}
        for key in ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens"):
            if suite_usage.get(key) != sum(case["usage"][key] for case in run["cases"]):
                raise ValueError(f"Suite {key} does not match case totals: {run['run_id']}")


def _render_run(run: dict, source_names: dict[str, str]) -> str:
    model = _cell(run["model"])
    reasoning = _cell(run["reasoning_level"])
    duration, tokens = _case_totals(run)
    lines = [
        f"# Benchmark run: {_cell(run['run_id'])}",
        "",
        f"Model: **{model}** · Reasoning: **{reasoning}** · "
        f"Harness: {_cell(run['harness'])} · Started: "
        f"{_cell(run['started_at'])}",
        "",
        "## Summary",
        "",
        "| Metric | Model | Reasoning | Time | Total tokens | Estimated cost |",
        "|---|---|---|---:|---:|---:|",
    ]
    for case in run["cases"]:
        lines.append(
            f"| {_cell(source_names[case['case_id']])} | {model} | {reasoning} | "
            f"{_seconds(case.get('duration_seconds'))} | "
            f"{_tokens(case.get('usage', {}).get('total_tokens'))} | "
            f"{_cost(case.get('estimated_cost_usd'))} |"
        )
    lines.extend(
        [
            f"| **Total** | **{model}** | **{reasoning}** | **{_seconds(duration)}** | "
            f"**{_tokens(tokens)}** | **{_cost(run.get('estimated_cost_usd'))}** |",
            "",
            f"Full suite wall time: {_seconds(run.get('duration_seconds'))}. "
            "The total above sums measured case times; the suite wall time can include gaps.",
            "",
            "Estimated cost is an API-equivalent model-token estimate, not a subscription charge. "
            "Cached input is included in input tokens. Rounded row costs may differ from the total.",
            "",
            "## Detail",
            "",
            "| Metric | Model | Reasoning | Search | Metadata | Retrieve | Input tokens | "
            "Cached input | Output tokens | Total tokens | Correct |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for case in run["cases"]:
        timing = case.get("tool_timing_seconds") or {}
        usage = case.get("usage") or {}
        lines.append(
            f"| {_cell(source_names[case['case_id']])} | {model} | {reasoning} | "
            f"{_seconds(timing.get('search_catalog'))} | "
            f"{_seconds(timing.get('get_metadata'))} | "
            f"{_seconds(timing.get('retrieve'))} | "
            f"{_tokens(usage.get('input_tokens'))} | "
            f"{_tokens(usage.get('cached_input_tokens'))} | "
            f"{_tokens(usage.get('output_tokens'))} | "
            f"{_tokens(usage.get('total_tokens'))} | "
            f"{'Yes' if case['correct'] else 'No'} |"
        )
    lines.extend(["", "## Run notes", "", _cell(run.get("notes") or "None recorded."), ""])
    if run.get("cost_basis"):
        lines.extend(["## Cost basis", "", _cell(run["cost_basis"]), ""])
    lines.extend(
        [
            "Full answers, dataset IDs, and retrieval artifact paths are in "
            "[results.json](../results.json).",
            "",
        ]
    )
    return "\n".join(lines)


def _render_history(runs: list[dict]) -> str:
    lines = [
        "# Benchmark performance over time",
        "",
        "Generated from [results.json](results.json). Each linked run report has a summary "
        "table and a detailed timing and token table.",
        "",
        "| Run | UTC start | Model | Reasoning | Correct | Total case time | Total tokens | "
        "Estimated cost | Suite wall time |",
        "|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for run in runs:
        duration, tokens = _case_totals(run)
        passed = sum(case["correct"] for case in run["cases"])
        report = f"reports/{_filename(run['run_id'])}"
        lines.append(
            f"| [{_cell(run['run_id'])}]({report}) | {_cell(run['started_at'])} | "
            f"{_cell(run['model'])} | {_cell(run['reasoning_level'])} | "
            f"{passed}/{len(run['cases'])} | "
            f"{_seconds(duration)} | {_tokens(tokens)} | "
            f"{_cost(run.get('estimated_cost_usd'))} | "
            f"{_seconds(run.get('duration_seconds'))} |"
        )
    lines.extend(
        [
            "",
            "Total case time sums each case's search through checked evidence. Estimated cost "
            "uses the model pricing saved with each run; it is not an actual charge. "
            "Compare run notes before attributing differences to a model.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if generated reports are stale")
    args = parser.parse_args()
    data = _read_json(RESULTS)
    source_names = {case["id"]: case["source"] for case in _read_json(CASES)["cases"]}
    runs = data["runs"]
    _validate(runs, list(source_names))
    expected = {HISTORY: _render_history(runs)}
    expected.update(
        {REPORTS / _filename(run["run_id"]): _render_run(run, source_names) for run in runs}
    )
    if args.check:
        stale = [
            str(path.relative_to(ROOT))
            for path, content in expected.items()
            if not path.exists() or path.read_text(encoding="utf-8") != content
        ]
        if stale:
            print("Stale benchmark reports: " + ", ".join(stale))
            return 1
        print(f"Benchmark reports current: {len(runs)} run(s)")
        return 0
    REPORTS.mkdir(parents=True, exist_ok=True)
    for path, content in expected.items():
        path.write_text(content, encoding="utf-8")
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
