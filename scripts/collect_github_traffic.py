#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FIELDS = ["timestamp", "count", "uniques", "collected_at"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _fetch_json(repo: str, metric: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/traffic/{metric}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "github-traffic-collector",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub traffic request failed for {metric}: HTTP {exc.code}: {body}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"GitHub traffic request for {metric} did not return a JSON object.")
    return payload


def _read_existing(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    rows: dict[str, dict[str, str]] = {}
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            timestamp = str(row.get("timestamp") or "").strip()
            if not timestamp or timestamp.upper() == "TOTAL":
                continue
            rows[timestamp] = {field: str(row.get(field) or "") for field in FIELDS}
    return rows


def _write_metric(path: Path, rows: dict[str, dict[str, str]], collected_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = [rows[key] for key in sorted(rows)]
    total_count = sum(int(row.get("count") or 0) for row in ordered)
    total_uniques = sum(int(row.get("uniques") or 0) for row in ordered)
    ordered.append(
        {
            "timestamp": "TOTAL",
            "count": str(total_count),
            "uniques": str(total_uniques),
            "collected_at": collected_at,
        }
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(ordered)


def _merge_metric(data_dir: Path, metric: str, payload: dict[str, Any], collected_at: str) -> None:
    series = payload.get(metric)
    if not isinstance(series, list):
        raise RuntimeError(f"GitHub traffic payload for {metric} does not contain a '{metric}' list.")

    rows = _read_existing(data_dir / f"{metric}.csv")
    for item in series:
        if not isinstance(item, dict):
            continue
        timestamp = str(item.get("timestamp") or "").strip()
        if not timestamp:
            continue
        rows[timestamp] = {
            "timestamp": timestamp,
            "count": str(int(item.get("count") or 0)),
            "uniques": str(int(item.get("uniques") or 0)),
            "collected_at": collected_at,
        }
    _write_metric(data_dir / f"{metric}.csv", rows, collected_at)


def main() -> int:
    parser = argparse.ArgumentParser(description="Persist simple GitHub clone/view totals.")
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY", ""), help="Repository in owner/name form.")
    parser.add_argument("--data-dir", default="github-traffic", help="Directory for traffic CSV files.")
    args = parser.parse_args()

    repo = args.repo.strip()
    token = (os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN") or "").strip()
    if not repo:
        print("Missing repo. Pass --repo owner/name or set GITHUB_REPOSITORY.", file=sys.stderr)
        return 2
    if not token:
        print("Missing token. Set GH_TOKEN or GITHUB_TOKEN with repository traffic access.", file=sys.stderr)
        return 2

    data_dir = Path(args.data_dir)
    collected_at = _utc_now()
    for metric in ("clones", "views"):
        _merge_metric(data_dir, metric, _fetch_json(repo, metric, token), collected_at)
        print(f"updated {data_dir / f'{metric}.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
