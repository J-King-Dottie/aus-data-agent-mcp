# GitHub traffic history

This directory stores daily snapshots from GitHub's repository traffic API.

GitHub only exposes recent traffic, so the workflow in `.github/workflows/collect-github-traffic.yml`
merges the current rolling window into CSV files once per day.

- `clones.csv`: daily clone counts and daily unique cloners
- `views.csv`: daily view counts and daily unique viewers
- `summary.json`: known totals from the saved CSV rows

The `uniques` values are daily unique counts. Summing them is useful as a daily activity signal, but it is not the same as all-time unique people because the same person can appear on multiple days.
