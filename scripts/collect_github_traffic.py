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
    url = f"https://api.github.com/repos/{repo}/traffic/{metric}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "nisaba-github-traffic-collector",
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
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = {}
        for row in reader:
            timestamp = str(row.get("timestamp") or "").strip()
            if timestamp:
                rows[timestamp] = {field: str(row.get(field) or "") for field in FIELDS}
    return rows


def _write_csv(path: Path, rows: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = [rows[key] for key in sorted(rows)]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(ordered)


def _merge_metric(data_dir: Path, metric: str, payload: dict[str, Any], collected_at: str) -> int:
    series = payload.get(metric)
    if not isinstance(series, list):
        raise RuntimeError(f"GitHub traffic payload for {metric} does not contain a '{metric}' list.")

    path = data_dir / f"{metric}.csv"
    rows = _read_existing(path)
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
    _write_csv(path, rows)
    return len(series)


def _write_summary(data_dir: Path, repo: str, collected_at: str, latest_payloads: dict[str, dict[str, Any]]) -> None:
    summary: dict[str, Any] = {"repo": repo, "collected_at": collected_at, "metrics": {}}
    for metric in ("clones", "views"):
        rows = _read_existing(data_dir / f"{metric}.csv")
        latest_payload = latest_payloads.get(metric) or {}
        summary["metrics"][metric] = {
            "days": len(rows),
            "known_total_count": sum(int(row.get("count") or 0) for row in rows.values()),
            "known_sum_daily_uniques": sum(int(row.get("uniques") or 0) for row in rows.values()),
            "latest_github_rolling_count": latest_payload.get("count"),
            "latest_github_rolling_uniques": latest_payload.get("uniques"),
            "first_timestamp": min(rows) if rows else None,
            "last_timestamp": max(rows) if rows else None,
        }
    (data_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Persist GitHub repository traffic beyond GitHub's rolling window.")
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
    latest_payloads: dict[str, dict[str, Any]] = {}
    for metric in ("clones", "views"):
        payload = _fetch_json(repo, metric, token)
        latest_payloads[metric] = payload
        merged = _merge_metric(data_dir, metric, payload, collected_at)
        print(f"{metric}: merged {merged} rows from GitHub's rolling window")
    _write_summary(data_dir, repo, collected_at, latest_payloads)
    print(f"wrote {data_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
