# CLAUDE.md — S.H.I.O.K. Index (agent instructions)

You are building the S.H.I.O.K. Index: a free, non-commercial civic web app that gives
every Singapore postal code an explainable "comfort score" for the walk to transit
(shelter from rain, heat, crossing friction, transit access, bus frequency).

Authoritative spec: `docs/PRD_v4.2.md`. Task order and acceptance criteria: `docs/BUILD_PLAN.md`.
Dataset details and gotchas: `docs/DATA_SOURCES.md`. If this file and the PRD conflict, the PRD wins.

## Hard constraints — never violate

1. **$0 budget.** Never introduce a paid service, a service requiring a credit card, or any
   metered resource without a hard free cap. Vercel **Hobby** tier only. No Cloudflare. No AWS/GCP.
2. **No GitHub Actions.** All compute (ingestion, network build, routing, scoring, export)
   runs natively on the owner's Windows 11 machine (no WSL, no Docker — see decisions.md). GitHub is code hosting only.
3. **Non-commercial.** No ads, analytics beyond a privacy-safe pageview counter, donations,
   accounts, or partner integrations. This is what makes Vercel Hobby compliant.
4. **No runtime backend** except one Vercel serverless function: `/api/onemap-search`
   (OneMap geocoding proxy with token cache + per-IP throttle). Everything else is static files.
5. **No date-stamped dataset URLs in code.** Discover latest via listing/API, download,
   SHA-256 hash; the hash is the change trigger and goes in `manifest.json`.
6. **All metric geometry ops in EPSG:3414 (SVY21).** WGS84 only at the serving edge.
7. **Secrets** live only in `.env` (gitignored) and Vercel env vars. Never commit keys.
   Never log the OneMap token.
8. **Licensing:** every LTA/data.gov.sg layer needs Singapore Open Data Licence attribution;
   OneMap/SLA attribution for basemap + search; OSM-derived geometries are ODbL.
   Keep `docs/ATTRIBUTION.md` current when adding any data source.
9. **Do not scrape, call, or depend on undercover.gov.sg.** It is a closed government
   prototype with no API; we use the same open upstream data directly.
10. **Determinism:** same inputs + same code tag ⇒ byte-identical artifacts
    (stable sort orders, fixed random seeds, versioned config).

## Stack (locked)

- **Pipeline:** Python 3.12 (managed by `uv`, `uv.lock` committed) — geopandas, shapely 2, pyproj, duckdb, python-igraph, h3; orchestrated by `python run.py <task>` (cross-platform; there is no make).
- **Router (amended 2026-07-25, owner sign-off):** both passes run in-process on the project's own
  conflated graph via python-igraph — weights `length_m` (shortest) and
  `sheltered_cost = length_m × (1 + λ×(1−covered))` (λ in params.yaml). No Valhalla, no OSRM:
  routing happens on the same edges we score, so covered ratios and exposure gaps need no map-matching.
- **Frontend:** Next.js (static-first) + TypeScript + MapLibre GL JS, deployed with `vercel`.
- **Artifacts:** plain `.json` under `web/public/data/` (Vercel edge compresses — never pre-gzip),
  hash-versioned filenames (see PRD §8 shapes).

## Repo layout

```
/pipeline        Python package: fetch, ingest, network, route, score, export, validate
/pipeline/config weights.yaml, params.yaml (all constants live here, never inline)
/web             Next.js app (app router), public/data/ artifacts land here
/docs            PRD_v4.2.md, BUILD_PLAN.md, DATA_SOURCES.md, ATTRIBUTION.md, decisions.md
/raw             immutable downloaded payloads by hash (gitignored)
/tests           pytest: unit tests for every scoring formula + golden set
run.py           task runner: check | ingest | network | route | score | export | validate | publish | test
```

## Working conventions

- Work task-by-task from BUILD_PLAN; one branch/commit per task, message `[T0.3] fetch-and-hash`.
- A task is DONE only when its acceptance criteria pass and `python run.py test` is green.
- Write the test for a scoring formula before the formula (they are pure functions — keep them so).
- Every pipeline stage prints a one-line summary (counts, timings) and writes a log under `logs/`.
- **Decide vs ask:** decide freely on implementation details (libraries within the stack, file
  layout, refactors). STOP and ask the owner before: adding any dependency with network calls
  at runtime, changing scoring weights/formulas, changing artifact schemas, anything touching
  constraints 1–9, or any deviation from a LOCKED PRD decision (log it in `docs/decisions.md`).
- Prefer boring code. No premature abstraction; the pipeline is a straight line.
- **Determinism spec (byte-identical artifacts):** chunk work by sorted postal code (never by worker
  count), sort all JSON keys and record orders, round floats at export (scores 1 dp, coords 5 dp),
  set `PYTHONHASHSEED=0`, pin dependencies via `uv.lock` + `package-lock.json`, pin Node (`packageManager` + `.nvmrc`).
  Golden-set tests assert ranges, not exact floats.
- **Windows-native rules:** pathlib for every path (never hardcoded separators); every `open()` uses
  `encoding="utf-8"` and `PYTHONUTF8=1` is set; multiprocessing is spawn-safe (`if __name__ == "__main__":`
  guards everywhere, workers receive plain arrays and rebuild the igraph graph per process — never pickle
  graph objects); enable long paths once (`git config core.longpaths true` + Windows LongPathsEnabled);
  `.gitattributes` enforces LF.
- `python run.py publish` runs `validate` first (hard-coded gate), then
  `vercel deploy --prod --archive=tgz`. It is the only deploy path.

## What NOT to build (scope guards)

- No turn-by-turn navigation, no live routing UI (route display is score *evidence* only).
- No database, no ORM, no auth, no user state, no cron on Vercel.
- No `GET /api/shiok/{postal}` endpoint — the frontend reads static JSON directly.
- No island-wide map tiles in MVP (Phase 2 item, feature-flagged).
- Night Safety is a map overlay only — never part of the composite score.
