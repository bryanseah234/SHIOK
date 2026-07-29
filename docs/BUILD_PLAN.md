# BUILD_PLAN.md — S.H.I.O.K. Index, Phase 0–1 (prototype)

Scope: Phase 0 (foundations) and Phase 1 (3-planning-area prototype on Vercel).
Phases 2–4 are specified in `PRD_v4.2.md` §14 and are OUT OF SCOPE for now.
Router amendment (2026-07-25, owner-signed): all routing runs in python-igraph on the project's
own conflated graph; Valhalla and OSRM are out of the stack (see ENGINEERING_REVIEW.md §1).
Pilot planning areas (typology spread): **Toa Payoh** (mature HDB, dense linkways),
**Bukit Timah** (landed/condo, sparse shelter — stress test), **Downtown Core**
(underground links, integrated developments).

Every task lists acceptance criteria (AC). Done = AC pass + `python run.py test` green + short note in `docs/decisions.md` if anything was decided.

---

## Milestone 0 — Foundations (target: ~week 1)

**T0.1 — Repo scaffold.**
Create the layout from CLAUDE.md; `run.py` task CLI with stub targets (publish hard-depends on validate); pre-commit (ruff, black, mypy loose); pytest wired; `.env.example` → `.env` loading via python-dotenv; commit `.gitattributes` (`* text=auto eol=lf`); README documents the Windows one-time setup (`git config core.longpaths true`, enable LongPathsEnabled, `PYTHONUTF8=1`).
AC: `python run.py test` runs (0 tests ok); `python run.py check` prints "not implemented" cleanly; `python run.py publish` visibly runs validate first; CI-free (no workflows dir).

**T0.2 — Environment lockdown (Windows-native, no Docker).**
Initialize the Python env with `uv` (pyproject + `uv.lock` committed): geopandas, shapely, pyproj, duckdb, python-igraph, h3, httpx, python-dotenv, pytest, ruff. Verify each imports on Windows; record versions in `docs/decisions.md`.
AC: `uv sync` reproduces the env from lockfile; an igraph shortest-path smoke test passes natively on a bundled 100-edge fixture; `python -c "import geopandas, igraph, duckdb, h3"` exits 0.

**T0.3 — Fetch-and-hash for all sources.**
First step (probe): settle DataMall mechanics empirically — try the geospatial zips unauthenticated, fall back to the `AccountKey` header; confirm the listing page as the discovery source; record findings in `docs/decisions.md`. Then implement `pipeline/fetch.py` per `DATA_SOURCES.md`: discover latest for each upstream dataset (now 10 LTA/data.gov.sg sources including #12 Planning Area Boundary), download to `raw/<sha256>/<name>`, write/update `raw/manifest.json` (source, url-as-discovered, sha256, bytes, fetched_at). Idempotent; `python run.py check` = discover+hash+diff only (no re-download when unchanged).
AC: two consecutive `python run.py check` runs report "no changes"; corrupting one local hash triggers exactly that source's re-download; manifest validates against a JSON schema in `tests/`.

**T0.4 — Vercel hello + OneMap proxy.**
Next.js app deployed to Vercel Hobby (owner runs `vercel login` first). Implement `/api/onemap-search`: caches the OneMap token (3-day expiry) in memory + re-auths on 401, per-IP throttle (e.g., 30 req/min), returns top-5 geocode candidates. Basic page with a search box calling it. Add a one-off local probe (`pipeline/probe_onemap.py`): controlled request ramp against OneMap search until 429, record the threshold and any `Retry-After` behavior, set the pipeline's client throttle to ~50% of the observed ceiling in `params.yaml`.
AC: deployed URL live on `*.vercel.app`; searching "Toa Payoh" returns candidates; OneMap credentials only in Vercel env vars; hammering the endpoint returns 429; probe findings recorded in `docs/decisions.md` and throttle configured.

**T0.5 — Attribution matrix.**
`docs/ATTRIBUTION.md`: table of dataset → licence → required attribution text → where it appears (site footer, dataset README). Footer component renders it.
AC: every source in DATA_SOURCES.md has a row; footer visible on the deployed page.

**T0.6 — Bulk postal geocode (one-time, resumable).**
No single open dataset covers all ~140k building postals (landed/private gaps). Build `pipeline/geocode.py`: iterate the postal universe (union of building datasets + generated candidates), query OneMap search per postal at the T0.4-probed throttle, cache every response permanently in `raw/geocode/` keyed by postal, resumable after interrupt. Expect ~10–24 h wall clock once; later runs touch only new postals.
AC: ≥ 99% of the postal list resolves to coordinates; failures logged with reason; re-running completes in minutes on cache hits.

---

## Milestone 1 — Network, routing, scoring, prototype UI (target: weeks 2–3)

**T1.1 — Pedestrian network build (pilot areas).**
From the Geofabrik/BBBike OSM extract + LTA Covered Linkway + Overhead Bridge/Underpass layers: build an edge list clipped to the 3 pilot areas (buffer 1 km), reprojected to EPSG:3414, each edge carrying `length_m`, `covered` (bool), `underground` (bool), source tags. Conflate LTA linkways onto OSM footways (spatial match, tolerance param in `params.yaml`); report LTA linkway length matched vs unmatched per area. Emit conflation QA artifacts: the two disagreement sets (OSM-covered-only vs LTA-only) as GeoJSON, and a synthesized-island report from connected-components analysis. Clip polygon = union of pilot planning-area boundaries (dataset #12), buffered ~500 m.
AC: `python run.py network --area pilot` emits `network.parquet` + a QA report (node/edge counts, dangling-edge count < threshold, % LTA linkway length matched ≥ 80% per area — if lower, flag for owner rather than silently proceeding); synthesized-island length ≈ 0 (every synthesized edge attached to the giant component).

**T1.2 — Dual routing on our own graph (amended — owner-signed).**
Both passes run in python-igraph over `network.parquet`: shortest by `length_m`; sheltered by `sheltered_cost = length_m × (1 + λ×(1−covered))`, λ from `params.yaml` (start 0.6). Per-origin Dijkstra with early termination at the node set (≤ ~1.5 km radius), multiprocessing chunked by sorted postal (Windows spawn-safe: `if __name__ == "__main__":` guard, workers get plain edge arrays and rebuild the graph per process — never pickle igraph objects). Post-filter: accept the sheltered path only if length ≤ 1.25× shortest, else score the shortest path throughout. No Valhalla, no OSRM — the routed edges are the scored edges by construction.
AC: for 20 hand-picked OD pairs across the pilots, both paths render sanely on a debug map; sheltered path's covered-ratio ≥ shortest's in ≥ 90% of cases; detour cap enforced (property test); pilot batch runtime extrapolates island-wide to < 3 h on 8 cores.

**T1.3 — Node-set selection.**
Per postal centroid: all exits of nearest MRT/LRT station; add second station's exits if within 1.2× distance of first; bus stops within 250 m routed meeting the frequency threshold (params). Output candidate set with routed distances.
AC: unit tests on synthetic layouts (multi-exit tie-break, second-station inclusion boundary); spot-check 10 real postals against manual expectation.

**T1.4 — Scoring engine.**
Pure functions per PRD §7 (Transit Access, Bus Connectivity expected-wait, Rain Shelter with exposure-gap extraction, Heat provisional covered-only, Crossing Friction with DBSCAN clusters + TYP_NAM filter + grade-separated exemption). Weights/breakpoints only from `config/weights.yaml`.
AC: pytest covers every formula incl. edge cases (0 buses, no MRT ≤ 1,200 m ⇒ `NO_TRANSIT_IN_RANGE`, fully covered path, 6+ crossings floor); property test: subscores ∈ [0,100]; composite = weighted sum exactly.

**T1.5 — Export artifacts (H3-adaptive sharding).**
Emit `web/public/data/scores/{area}.json` (~55 files island-wide) and geometry sharded by **H3 res-8** at `web/public/data/geom/h3/{cell}.json` (polyline-encoded geometries + gaps); any res-8 cell whose file exceeds 250 KB raw is replaced by its res-9 children, listed in a small `geom/index.json`; plus `manifest.json` per PRD §8. Ship plain `.json` — Vercel edge compresses; never pre-gzip.
AC: JSON schema validation in tests; export-step assertions: total data-file count < 5,000 and every file < 5 MB; pilot artifact totals reported; a record round-trips (decode polyline ⇒ matches source geometry within 1 m); `python run.py publish` invokes `vercel deploy --prod --archive=tgz` and hard-depends on validate.

**T1.6 — Prototype frontend.**
Search (via proxy) → resolve postal → load area score file → compute the geometry shard client-side with `h3-js` (res-8; res-9 child if listed in `geom/index.json`) and lazy-load it → score card (total, 5 bars, top-2 plain-language reasons) → map: OneMap basemap, shortest + sheltered routes (pattern + colour, never colour alone), exposure gaps highlighted; compare mode (two postals side-by-side); "Data as of" from manifest; `NOT_YET_SCORED` / `NO_TRANSIT_IN_RANGE` states designed, not ad hoc.
AC: deployed to Vercel; Lighthouse a11y ≥ 90; keyboard-only walkthrough works; per-lookup network transfer ≤ 500 KB (measured, documented).

**Deferred frontend/router backlog — owner tabled on 2026-07-28.**
- User-facing shelter mode selector after MVP route UX is stable: default `Balanced` uses the PRD detour cap (`+25%` extra walk); optional later `Max shelter` mode may allow a higher cap such as `+50%`; always display the absolute extra walk, e.g. `+38 m`.
- Island-wide SHIOK map layer after MVP: optional comfort heatmap, shelter overlay, Rain Mode coverage, and browse-before-search visual coverage. This is separate from the OneMap basemap; it likely requires PMTiles or another static tile artifact plus a free-tier serving plan.
- Public route geometry contract upgrade: export covered/exposed segment geometry for both `Shortest` and `Shiokest`, not only full route polylines plus Shiokest exposure gaps.

**Open production-readiness backlog — owner tabled on 2026-07-28.**
- Keep Vercel automatic Git deployments as the target workflow, but make them production-safe first: set/verify the Vercel project root directory as `web` and choose a real static-data strategy for Git builds (`web/public/data` committed, downloaded from an approved artifact source, or served from a stable data deployment) so future commits cannot deploy a UI without score data.
- Improve compare mode UX: comparing A and B should show two score cards at once, with the map focused on both selected `Shiokest` routes and a clear active/inspect affordance for each address.
- Reduce map visual noise so routes are easier to see: investigate a toned-down OneMap style, raster desaturation/opacity treatment, or a neutral overlay that preserves attribution and legibility while making route evidence visually dominant.
- Remove preset/demo postal-code chips from the production UI; search should be the primary entry point, with no mock/preset-looking shortcuts.
- Audit and fix owner-verified shelter false negatives, starting with `S560234` → Mayflower MRT Exit 5: current output marks only 25.6 m / 3.1% covered, but local ground truth says the walk can use sheltered overpass and HDB block/void-deck paths. Diagnose whether the failure is missing graph geometry, missing `is_covered` attribution, disconnected covered edges, origin/exit snapping, or shelter-lambda/detour tuning before changing scores.
- Complete the 2,000-postal OneMap validation gate from PRD §2/§12: stratified sample, cached OneMap walk-route responses, median ≤ 10%, p95 ≤ 25%, and publish blocked if thresholds fail.
- Close the legitimate full-postal-universe gap: find a real source for the remaining gap toward ~140k Singapore postals, or continue shipping honest `NOT_YET_SCORED` states without brute-forcing OneMap.
- Run and fix the Lighthouse/accessibility pass: Lighthouse a11y ≥ 90, keyboard-only walkthrough, text equivalents for map answers, reduced-motion support, and mobile readability.
- Add top-2 plain-language reasons per score, e.g. "Low because: 180 m exposed near the canal; 1 uncovered crossing."
- Keep the island-wide SHIOK tile layer / Rain Mode as an optional later feature after the selected-route UX and validation gates are stable.
- Track weather/time mode permutations, NParks Leaf Area Index shade work, live bus-arrival collection, missing network features, and human route-feedback flow in `docs/COMFORT_MODES_AND_DATA_QA.md`.

**T1.7 — Validation harness (pilot gate).**
Stratified 200-postal sample across the 3 areas; compare shortest-path distance vs OneMap `routeType=walk` (throttle from the T0.4 probe; responses cached by OD-pair hash in `raw/validation/`; resumable after interrupt). Report median + p95 deviation per area. Golden set: 15 hand-verified addresses with expected score ranges; wire into `python run.py validate`.
AC: median ≤ 10% and p95 ≤ 25% per area (if an area fails: investigate conflation first; only then propose OneMap-fallback for that area to owner); golden set green; validate blocks publish on failure (hard-coded in run.py).

---

## Definition of Phase-1 done

All T1.x AC green; the deployed prototype answers "how comfortable is this Toa Payoh / Bukit Timah / Downtown postal?" end-to-end from real data with provenance shown; `python run.py publish` is the only deploy path; $0 spent.

## Explicit non-goals for this plan

Full-island batch (Phase 2), sensitivity analysis/kill rule (Phase 2 gate), island-wide Rain Mode overlay (Phase 2, Vercel Blob), canopy/shadow Heat terms and Live Comfort (Phase 4), any API for third parties (never).
