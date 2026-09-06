# ops/

Operational artefacts that are not part of the pipeline itself.

- `refresh-history.md` — one line per GitHub Actions run, appended by
  `nightly-refresh.yml` / `weekly-discovery.yml`. Its real job is to be a commit:
  GitHub disables scheduled workflows on a public repo after 60 idle days, and
  that commit resets the clock.
- `s3-lifecycle.json` — the bucket's retention policy. See below.

## S3 lifecycle policy

Apply (or re-apply after editing) with:

```bash
aws s3api put-bucket-lifecycle-configuration \
  --bucket "$S3_BUCKET" --region eu-central-1 \
  --lifecycle-configuration file://ops/s3-lifecycle.json
```

Read it back with `aws s3api get-bucket-lifecycle-configuration --bucket "$S3_BUCKET"`.

### Why it exists

The nightly job writes a full ~114 MB partition per run and nothing ever expired
it, so the bucket grew ~110 MB/day without bound (106 MB → 1,007 MB between
2026-08-26 and 2026-09-05). The three expiring prefixes are ~85% of that.

This does not violate "Bronze is immutable" — nothing is edited or overwritten,
old partitions simply stop being retained. The cost is that a Silver partition
older than 30 days can no longer be rebuilt from source. Raise `Days` to 90 if
that reproducibility window matters more than the storage; it still caps growth.

### Which prefixes must NEVER be added here, and why

**`bronze/person_details/` and `bronze/company_details/` are read across *every*
partition, not just the current one.** Two things depend on that:

1. `transform_people_details.py` / `transform_companies.py` list the whole prefix
   to build their Silver output — enrichment accumulates across partitions rather
   than living in one.
2. `ingest_people.py` / `ingest_companies.py` list the same prefix as their
   "already enriched, don't re-fetch" skip-list.

Expiring them would therefore both blank out `dim_person`'s bios and re-trigger
~35k TMDB calls. Every other Bronze prefix is read only at
`ingestion_date=<today>`, which is what makes expiring it safe.

`silver/` and `gold/` are left alone: `etl/incremental.pending_partitions()`
walks `silver/<entity>/` to find work, and they are small next to Bronze.
