# GitHub traffic history

This directory stores daily snapshots from GitHub's repository traffic API.

GitHub only exposes recent traffic, so the workflow in `.github/workflows/collect-github-traffic.yml`
merges the current rolling window into CSV files once per day.

- `clones.csv`: daily clone counts and daily unique cloners
- `views.csv`: daily view counts and daily unique viewers
- `popular_paths.csv`: daily snapshot of GitHub's current top viewed paths
- `popular_referrers.csv`: daily snapshot of GitHub's current top referrers
- `summary.json`: known totals from the saved CSV rows

The `uniques` values are daily unique counts. Summing them is useful as a daily activity signal, but it is not the same as all-time unique people because the same person can appear on multiple days.

GitHub exposes path and referrer traffic as a short rolling top list, not a permanent history. The snapshot CSVs preserve each daily top list so visitor sources and pages can be tracked over time.

The workflow needs a `TRAFFIC_TOKEN` repository secret with access to the repository traffic API.
