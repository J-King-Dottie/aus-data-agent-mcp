# GitHub Traffic

This directory stores the simple GitHub traffic numbers we care about.

The workflow in `.github/workflows/collect-github-traffic.yml` runs once per day and merges GitHub's rolling traffic window into:

- `clones.csv`: total clones and unique cloners by day
- `views.csv`: total views and unique viewers by day

Each CSV ends with a `TOTAL` row. `count` is the total event count. `uniques` is the sum of GitHub's daily unique count, so it is a useful activity signal but not a true all-time person-level dedupe.

Interpretation:

- Views are a rough signal of human browsing because they usually require the GitHub page to be opened.
- Clones are a stronger signal of intentional use because something chose to fetch the repo locally.
- Low views plus high clones can suggest AI-agent use: agents can discover and inspect repos through search/API/tooling without creating many page views, then clone when the repo is useful.
