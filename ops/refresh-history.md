# Refresh run history

Appended one line per run by `.github/workflows/nightly-refresh.yml` and
`weekly-discovery.yml`. Committing this file is also what resets GitHub's
60-day scheduled-workflow inactivity timer on a public repo.

Format: `<UTC timestamp>  <status>  <run label>  (<trigger>)`
