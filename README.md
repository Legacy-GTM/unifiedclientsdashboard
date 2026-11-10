# Legacy GTM client dashboard (GitHub Actions + Pages, free)
`refresh.yml` runs every ~2 min (triggered externally; GitHub's own timer is a slow fallback): runs `refresh.py` (Instantly + Sendkit, skipped leads excluded), then force-pushes the built site (`index.html`
+ `data.json` etc.) to the `gh-pages` branch as ONE orphan commit with a new SHA every run; Pages deploys from that branch. API keys live only in repo Actions secrets (INSTANTLY_API_KEY, SENDKIT_API_KEY, later CLOSE_API_KEY);
they are never written to the site. Previous data/history are read back from `gh-pages` (live site as fallback).
Setup: Settings > Pages > Source = "Deploy from a branch", branch `gh-pages`, folder `/`; Settings > Secrets and variables > Actions > add the secrets; Actions > refresh-dashboard > Run workflow.
