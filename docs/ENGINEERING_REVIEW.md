# ENGINEERING_REVIEW.md — Pre-docker-compose review of S.H.I.O.K. Phase 0/1
Role: Lead Systems Engineer. Scope: Gemini's three execution vectors. Product scope not relitigated.
Markers: **[VERIFIED]** = checked against primary docs/source, 2026. **[JUDGMENT]** = engineering opinion. **[UNVERIFIED]** = could not confirm; empirical probe prescribed.

---

## VECTOR 1 — The router question, answered honestly, then the pipeline failure modes

### 1.1 The premise in the prompt is wrong: there is no "custom Valhalla dynamic costing profile"
**[VERIFIED]** Valhalla has no user-supplied costing system — no Lua, no plugin profiles. Costing models are compiled C++ (`src/sif/pedestriancost.cc`). What you get at request time is a fixed set of pedestrian knobs: `walkway_factor` (default 0.9), `sidewalk_factor`, `alley_factor` (2.0), `driveway_factor` (5.0), `step_penalty`, elevator penalty, `use_lit`, `shortest`, etc. None of them reads `covered=*`. The graph builder does not persist a `covered` edge attribute your costing could see.

**[VERIFIED]** The one exploitable hook: Valhalla *does* persist `lit` on edges, and `use_lit` converts to an `unlit_factor_` applied as `factor *= edge->lit() + (!edge->lit() * unlit_factor_)` (PR #3957). So the only no-C++ "Valhalla-native" play is the **tag-laundering hack**: preprocess the PBF, set `lit=yes` on covered/underground edges and strip it elsewhere, then run the sheltered pass with `use_lit` high against a second tileset.

**[JUDGMENT] Why the hack fails our requirements:** (a) the unlit factor's strength is bounded by the implementation, not by you — you cannot guarantee it produces detours equivalent to λ≈0.6, and tuning it means rebuilding tilesets per iteration; (b) you are destroying real `lit` semantics you may want later for the Night overlay; (c) you now maintain two tilesets plus a PBF-rewriting step; (d) worst of all, see 1.2: whatever Valhalla returns still has to be mapped back onto *your* edge list to compute covered ratios and exposure gaps.

### 1.2 The decisive argument nobody's made yet: scoring already forces you to own the graph
The Rain Shelter ratio, exposure gaps, Heat weighting, and Crossing Friction are all computed **per edge of the conflated network you build yourself** (OSM + LTA linkways + synthesized segments). If routing happens inside Valhalla (or OSRM), you get back a *geometry*, and you must map-match that polyline onto your own edges to attribute coverage — a whole error class (ambiguous matches at parallel paths, bridges over walkways, tolerance tuning) invented purely by routing in a different graph than you score in. And LTA-only linkways with no OSM counterpart must be written **back into a PBF** with synthetic node/way IDs just so Valhalla can see them — a second error class (ID collisions, connectivity snapping inside a format that fights you).

Route on the graph you already own and both error classes vanish: the path *is* a list of your edges; covered ratio and gaps are a `groupby`.

### 1.3 Router recommendation and formal amendment (needs owner sign-off — amends a LOCKED decision)
Comparison for a 140k-origin × ~10-target offline batch, 8 cores:

| Option | Verdict |
|---|---|
| (i) Valhalla-only (lit-hack, two tilesets) | Rejected. Uncontrollable penalty strength, tileset churn, map-match + PBF write-back failure classes. C++ custom costing = days + a maintained fork — violates the project's complexity budget. **[JUDGMENT]** |
| (ii) OSRM + custom Lua foot profile | Genuinely viable: Lua profiles can read arbitrary tags (`way:get_value_by_key("covered")`) and scale `rate` — this is the textbook OSRM use case. But: profile binds at `osrm-extract`, so two profiles = two full MLD builds (extract/partition/customize ×2); geometry still needs map-matching back to your edges; another container. **[JUDGMENT, OSRM Lua capability well-established]** |
| (iii) Own graph, igraph (C core) with two weight columns | **Ship this.** Singapore pedestrian graph ≈ low-hundreds-of-thousands of edges after clipping; per-origin Dijkstra with early termination at ~10 nearby targets (≤ ~1.5 km radius) is sub-10ms in igraph's C core; 140k origins × 2 passes across 8 processes lands comfortably inside the < 3 h budget with headroom. Deterministic (you control tie-breaking), zero extra containers, and the scored edges are the routed edges by construction. **[JUDGMENT with high confidence]** |

**Proposed amendment wording:** *"R1-LOCKED 'Valhalla in Docker' is amended at implementation level: both routing passes execute on the project's own conflated graph via python-igraph (weight columns: `length_m`; `sheltered_cost = length_m × (1 + λ·(1−covered))`, λ from params.yaml). Valhalla is removed from the stack. OneMap walk-routing remains the sole external validation baseline (unchanged gates). Rationale: scoring requires per-edge attribution on our graph regardless; routing in any external engine adds geometry→edge map-matching and PBF write-back of synthesized linkway edges — two failure classes deleted by routing where we score."* This also deletes the Valhalla service from docker-compose before it's written — the compose file shrinks to one `pipeline` image.

### 1.4 Conflation failure modes (the actual hard part) — each with the fix
1. **LTA Covered Linkway is canopy POLYGONS, not centerlines.** Skeletonization via the Python `centerline` package (Voronoi-based) produces spikes and stub branches on long thin polygons and falls apart on multipolygon slivers. Fix: `shapely.make_valid` → explode multiparts → densify boundary (~1 m) → centerline → prune branches shorter than a threshold (~5 m) → simplify (~0.5 m). Then **prefer matching over synthesizing**: only skeletonize linkway polygons that match *no* OSM footway. **[JUDGMENT; centerline's thin-polygon spike behavior is a known characteristic]**
2. **Match step:** buffer each linkway polygon by ~2–3 m; an OSM footway edge is "covered" if ≥ ~60% of its length falls inside. OSM ways split at every junction, so expect partial coverage — attribute per-edge, don't force whole-way decisions. Precedence rule: `covered = OSM.covered=yes OR LTA-match` (boolean OR); log the two disagreement sets (OSM-only, LTA-only) as a QA artifact — the Citymapper import means heavy overlap is expected, and the OR makes double-counting harmless. **[JUDGMENT]**
3. **LTA-only segments → synthesized edges in OUR graph** (never back into a PBF now): snap endpoints to the nearest graph node within ~2 m; if none, split the nearest edge within ~5 m and insert a node; beyond that, the segment stands alone — and step 4 catches it.
4. **Connectivity gate (non-negotiable):** after the build, run connected components. Every synthesized edge not attached to the giant component is a routing landmine (unreachable "sheltered" segments silently ignored, or worse, origins snapped onto islands). QA report must list island count/length; pilot exit criteria should require ~0 synthesized-island length. **[JUDGMENT]**
5. **Vertical separation:** build the graph from OSM **topology** (shared node IDs), never from geometric intersection — otherwise every overhead bridge creates a phantom junction with the road below. `layer=*`/`bridge`/`tunnel` are only needed if you geometrically intersect; with topology-only you get this for free. Same reason: never node-snap synthesized linkway ends to a road they merely cross under.
6. **Access filtering:** drop `access=private|no` (unless `foot=yes`), `barrier=*` nodes that block traversal (gates without `foot=yes`), construction. Keep `highway=steps` (score-neutral; the step-penalty debate is a later calibration question).
7. **Shapefile hygiene:** LTA `.prj` files can be missing/odd — **force** EPSG:3414 on read, verify by asserting coordinates fall inside SVY21's Singapore envelope (roughly X 2k–56k, Y 15k–52k) and hard-fail otherwise; DBF text may be CP1252 — read with explicit encoding and log replacements. **[JUDGMENT; both are recurring LTA-shapefile complaints]**
8. **The misspelled filename** (`Pedestrain...`) is already noted in DATA_SOURCES — keep matching by fuzzy listing, not exact name.

### 1.5 WSL2/Docker realities
1. **Keep the repo and all data inside the WSL ext4 filesystem (`~/`), never under `/mnt/c`.** Cross-OS 9P bind mounts are notoriously slow for many-small-file geospatial I/O — order-of-magnitude-class slowdowns are widely reported. This is the single biggest local-performance decision. **[JUDGMENT; well-known WSL2 characteristic]**
2. `.wslconfig`: give WSL ≥ 12–16 GB (`memory=16GB`) — the conflation step (geopandas spatial joins island-wide) spikes far above the routing step. Dropping Valhalla removes tile-build memory concerns entirely.
3. **CRLF will break the Makefile and every shell script.** `.gitattributes` with `* text=auto eol=lf` committed in T0.1, before any script exists.
4. Compose pattern: `pipeline` is a **one-shot job container** (`docker compose run pipeline make route`), not a long-running service — no healthchecks needed once Valhalla is gone. amd64 images are a non-issue on Windows/WSL2.

---

## VECTOR 2 — Static serving: the real limits and the layout that fits them

### 2.1 Verified Vercel constraints
- **[VERIFIED]** CLI deployments cap at **15,000 source files**; exceeding it fails the build. (No upper limit on *build output* files, but our data ships as source in `public/`.) Use `vercel deploy --archive=tgz` regardless — it collapses thousands of per-file upload API calls (a documented rate-limit failure mode at ~15k files) into one archive.
- **[VERIFIED]** Serverless function request/response body cap is **4.5 MB** — reconfirming: never stream data through a function; the OneMap proxy returns tiny payloads only.
- **[VERIFIED]** Hobby allows **12 serverless functions** per deployment — we use 1.
- Historic **100 MB per-file** upload ceiling: treat as real; no single artifact should approach it. **[VERIFIED as recurring limit reports; exact current figure varies by path]**
- Compression: ship plain `.json` and let Vercel's edge apply gzip/brotli per request — do **not** pre-gzip with `.gz` names (breaks content negotiation). **[JUDGMENT; standard platform behavior]**

### 2.2 The math that breaks the current layout
~140k postals include every landed house, so postal counts skew brutally: landed-heavy areas (Bedok, Serangoon, Bukit Timah, Hougang) plausibly run 8,000–15,000 postals. At ~0.6–1.2 KB of geometry per postal (two encoded polylines + gaps + node metadata), the worst `geom/{area}.json` is **8–18 MB raw**. `JSON.parse` of that on a mid-range Android is a 300–800 ms main-thread stall plus 3–6× memory inflation — a visible jank spike for *one lookup*, most of which the user never views. **[JUDGMENT on figures; the shape of the problem is not in doubt.]** Per-planning-area geometry is the wrong shard key. Scores (~150–300 B/postal) survive per-area: worst case ~2.5–4 MB raw → a few hundred KB compressed, loaded once per area and reused across lookups/compare — acceptable.

### 2.3 Chosen layout (decision)
- `/data/scores/{planning_area}.json` — ~55 files, lazy per lookup, cached in-app. Unchanged.
- `/data/geom/h3/{cell}.json` — **geometry sharded by H3 resolution 8** (~0.74 km²/cell; ≈ 1,100–1,500 land cells → files mostly 10–150 KB). **Adaptive split:** any res-8 cell whose file exceeds ~250 KB raw is replaced by its res-9 children (landed-dense pockets), listed in a tiny `/data/geom/index.json` (the only lookup table; a few KB). Client computes the cell with `h3-js` from the geocoded lat/lng — no 140k-row postal→shard index needed.
- File count: ~1.3–3k data files + app ≪ 15,000. Worst per-lookup transfer: one score file (~100–700 KB compressed, amortized) + one geom shard (~5–60 KB compressed) → **typically 100–400 KB**, parse cost single-digit ms — no Web Worker, no binary format needed. Flatgeobuf/protobuf would be solving a problem this sharding already deleted. **[JUDGMENT]**
- Bandwidth: at ~300 KB/lookup, Hobby's 100 GB/month ≈ 300k+ lookups; on exhaustion Vercel pauses rather than bills — acceptable failure mode, documented in the PRD.
- Range requests / packed binaries / Vercel Blob: **not needed** in this layout. Blob (1 GB / 10 GB-transfer free) stays reserved for the Phase-2 island-wide PMTiles overlay only.

---

## VECTOR 3 — What Week 1–2 is actually missing

1. **Planning-area boundaries were never in the data list.** Partitioning, per-area exports, and QA reconciliation all require them. **[VERIFIED]** data.gov.sg hosts *Master Plan 2019 Planning Area Boundary (No Sea)* as GeoJSON (~2 MB), URA, dataset id `d_4765db0e87b9c86336792efe8a1f7a66`, and the platform's poll-download API pattern (`api-open.data.gov.sg/v1/public/api/datasets/{id}/poll-download` → short-lived signed URL) is confirmed by the dataset pages' own sample code. Note: some MP2019 layers are marked superseded by **Draft Master Plan 2025** — fetcher should prefer a DMP25 planning-area layer if published, else MP2019. → Add as dataset #12.
2. **The postal-centroid source of record is weaker than assumed.** No single data.gov.sg file reliably covers all ~140k building postals (HDB datasets cover HDB; landed/private coverage is patchy). Reality: a **one-time bulk OneMap geocode** (search by postal, cache forever in `raw/geocode/`) is almost certainly required to reach full coverage. This is a new, explicit task — not a footnote in T1.3.
3. **OneMap rate limits: [UNVERIFIED].** The floating "250 req/min" figure never got authoritative confirmation. Prescription: T0.4 gains a 10-minute probe — controlled ramp against the search endpoint, observe 429/`Retry-After` behavior, set the client throttle to ~50% of observed ceiling. Plan wall-clock accordingly: at 100–250 req/min, 140k geocodes = 10–24 h once, resumable via cache; the 2,000-route validation = 15–40 min. Never run either inside the Vercel function path.
4. **DataMall geospatial discovery: [UNVERIFIED mechanics].** Whether the static zips need the `AccountKey` header and whether a machine-readable listing exists (vs scraping the Static Datasets page) must be settled empirically in T0.3's first hour — the fetcher should try unauthenticated first, fall back to header-authenticated, and treat the listing page as the discovery source with fuzzy name matching.
5. **OSM extract clipping.** Geofabrik's `malaysia-singapore-brunei` must be clipped (`osmium extract -p singapore.poly`) or you inherit Johor Bahru edges and Causeway artifacts; derive the clip polygon from the MP2019 planning-area union (buffered ~500 m) — one less external file. BBBike's Singapore extract is an acceptable alternative but pin one choice for reproducibility.
6. **Determinism spec is implicit but not written:** fixed process-count-independent chunking (chunk by sorted postal, not by worker), sorted JSON keys and record order, floats rounded at export (1 dp scores, 5 dp coords), `PYTHONHASHSEED=0`, pinned base image digests, pinned Node via `packageManager` + `.nvmrc`. Golden-set assertions compare against ranges, not exact floats, to survive libc/BLAS drift.
7. **Publish gating exists in prose, not mechanics:** `make publish` must be the *only* deploy path and must dep-chain `validate` (Make prerequisite, not convention), and use `--archive=tgz --prod`. `.vercel/` project linking is committed-ignored; `vercel login` is a Phase-0 human step (already listed).
8. **DBSCAN eps sanity:** eps=20 m is only meaningful post-reprojection — assert CRS==3414 immediately before clustering (cheap assert, catches the classic silent-degrees bug that makes one cluster of all Singapore).

### Do this before writing docker-compose.yml (ordered)
1. Owner signs off the router amendment (§1.3) — it deletes the Valhalla service from compose.
2. Commit `.gitattributes` (LF) + `.wslconfig` guidance; move/clone repo into WSL ext4.
3. Add dataset #12 (MP2019/DMP25 Planning Area Boundary) to DATA_SOURCES + fetcher config.
4. Add task T0.6 "bulk postal geocode (one-time, cached, resumable)" and the T0.4 rate-limit probe.
5. Write the determinism spec (§3.6) into CLAUDE.md conventions.
6. Decide the OSM extract source (Geofabrik+clip vs BBBike) and pin it.
7. Rewrite T1.5's artifact layout to the H3-adaptive sharding (§2.3); note `--archive=tgz` in `make publish`.
8. Empirically settle DataMall download mechanics (first hour of T0.3).
9. Then, and only then, write compose — which is now one `pipeline` service and nothing else.

### BUILD_PLAN edits (by task)
- **T0.2:** delete the `valhalla` service; single one-shot `pipeline` job container; drop the Valhalla `/status` AC, replace with "igraph shortest-path smoke test on a 100-edge fixture."
- **T0.3:** add planning-area boundary source; add the DataMall auth/listing probe as an explicit first step; AC unchanged.
- **T0.4:** add the OneMap 429 probe + throttle configuration AC.
- **NEW T0.6:** bulk geocode job — resumable, cached, throttled; AC: ≥ 99% of postal list resolves; failures logged with reason.
- **T1.1:** add conflation QA artifacts (disagreement sets, synthesized-island report); AC adds "synthesized-island length ≈ 0."
- **T1.2:** replace with the igraph dual-weight implementation; keep λ in params; the "timebox + fallback" language is obsolete — the fallback IS the design.
- **T1.5:** replace per-area geometry with H3-res-8 adaptive sharding + `geom/index.json`; add file-count and max-file-size assertions to the export step's AC.
- **T1.6:** add `h3-js` cell computation client-side; per-lookup transfer AC unchanged (≤ 500 KB) and now comfortably met.
- **T1.7:** unchanged, plus cache-and-resume explicitly required for the OneMap comparison run.
