# Legacy GTM client dashboard (GitHub Actions + Pages, free)
`refresh.yml` runs every ~10 min: runs `refresh.py` (Instantly + Sendkit, skipped leads excluded), then publishes `index.html`
+ `data.json` to GitHub Pages. API keys live only in repo Actions secrets (INSTANTLY_API_KEY, SENDKIT_API_KEY, later CLOSE_API_KEY);
they are never written to the site. Previous data/history are read back from the live site on each run.
Setup: Settings > Pages > Source = "GitHub Actions"; Settings > Secrets and variables > Actions > add the secrets; Actions > refresh-dashboard > Run workflow.
