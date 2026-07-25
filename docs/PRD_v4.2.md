# S.H.I.O.K. Index — Product Requirements Document

**Version:** 4.2
**Status:** Working draft — model and scoring LOCKED (v4.1 cross-review); positioning, hosting, and scope revised per Undercover deep-dive + owner decisions of 2026-07-25
**Date:** 2026-07-25
**Owner decisions driving this version:** (1) B2B is cut — S.H.I.O.K. is a free, non-commercial civic project; (2) host on Vercel Hobby wherever possible, everything else runs locally (native Windows Python — see §15 amendments); (3) GitHub Actions removed from the pipeline (minutes exhaustion); (4) all Undercover learnings accepted.

---

## 0. Change Log v4.1 → v4.2

| Area | v4.1 | v4.2 |
|---|---|---|
| Commercial posture | B2B embeds (99.co / PropertyGuru), counsel review before licensing | **Non-commercial, free, no monetization ever (ads/donations/paid features all excluded).** B2B persona, embed widget, and convenience API deleted. |
| Positioning | "No incumbent" for sheltered walkability | **Rewritten post-Undercover:** S.H.I.O.K. is the per-address comfort *index*; Undercover (OGP prototype) is the per-trip *router*. Complement, not compete. |
| Hosting (runtime) | Cloudflare Pages + R2 + Workers | **Vercel Hobby**: static frontend + data artifacts in the deployment; one serverless function (OneMap token proxy). Optional Phase-2 shelter overlay in Vercel Blob free tier. **No Cloudflare.** |
| Hosting (batch) | GitHub Actions or local | **Local only (native Windows Python; no Docker/WSL).** GitHub used for code hosting, not compute. |
| Convenience API `GET /api/shiok/{postal}` | Kept for B2B ergonomics | **Deleted.** Frontend reads static JSON directly. |
| Heat Comfort | Static tiers (underground/covered/canopy Phase 4) | + **Building-shadow exposure term** (OSM/Overture building heights + solar position, representative hours) in Phase 4 — adopted from Undercover's approach, built independently. |
| Route display | Product feature | **Reframed as score evidence** — justifies the Rain Shelter number and shows exposure gaps; explicitly not a navigation product. |
| Validation | OneMap quantitative gates + typology audit | + **Undercover as a qualitative baseline** inside the 50-address typology audit (manual spot-check; it has no API). |
| Map layers (MVP) | Island-wide PMTiles on R2 | **No island-wide tiles at MVP.** Per-postal route geometry fetched on demand as tiny static JSON. Generalized shelter-union PMTiles = optional Phase 2 via Vercel Blob. |

Everything not listed above — the model decisions D1–D4, the scoring weights and formulas, the validation gates, the provenance mechanism — is **unchanged and remains LOCKED** from v4.1.

---

## 1. Executive Summary

**Objective:** a repeatable, auditable, *explainable* index measuring the comfort — shelter, heat, crossing friction — and access quality of the walk between any Singapore postal code and its relevant transit nodes. Free to use, free to run, open data in and open data out.

**What S.H.I.O.K. is (post-Undercover):** the *score attached to a place*. The government's Undercover prototype (OGP, Hack for Public Good 2026) answers "how do I get from A to B staying shaded/sheltered right now?" S.H.I.O.K. answers "how structurally comfortable is *this address* for daily transit life?" — decomposed, comparable across addresses, stable over time, and published as an open dataset. One is a trip tool; the other is a place index. They validate each other's premise and do not overlap in function.

**Architecture in one line:** Local Windows-native batch (Python: igraph routing + spatial pipeline) → static artifacts (per-planning-area JSON, per-postal route geometry, provenance manifest) → deployed to Vercel Hobby → MapLibre frontend with OneMap basemap. Standing cost: **US$0/month, no card on file anywhere.**

---

## 2. Goals & Success Metrics

**Goals**

1. Explainable comfort score for every postal code, every deducted point traceable to a specific segment or crossing on a real path.
2. Zero standing cost and zero billable surfaces: all free tiers used are **hard-capped** (they pause, they never invoice).
3. Reproducibility: any published score re-derivable from the versioned inputs + tagged code in its provenance manifest.
4. Non-commercial by construction (see §11) — which is also what makes Vercel Hobby hosting legitimate.

**Success metrics**

- **Latency:** p95 postal lookup < 200 ms (static asset fetch from Vercel's CDN).
- **Coverage:** 100% of postal centroids return a score or an explicit state (`NO_TRANSIT_IN_RANGE`, `NOT_YET_SCORED`). Zero silent failures.
- **Routing validity (launch gate):** vs OneMap walk-routing on a 2,000-postal stratified sample — median routed-distance deviation ≤ 10%, p95 ≤ 25%.
- **Comprehension:** 90% of test users identify the dominant reason for a low score within 10 seconds.
- **Freshness:** re-scored within 14 days of a detected upstream change (manual local run cadence; see §6); "Data as of {date}" on every result.
- **Budget:** $0. A month in which any bill is generated is a failed month.

---

## 3. Users & Use Cases

- **Residents & house-hunters (primary):** "Can I walk to the MRT without an umbrella?" — answered with the score, the actual sheltered route, and its exposure gaps. Compare two addresses side by side.
- **Curious public:** explore the map, understand why their block scores the way it does.
- **Urban planners & researchers (via the open dataset):** identify high-density, low-shelter clusters; download per-planning-area aggregates and the full scored dataset under attribution.

*(Removed in v4.2: the B2B persona. 99.co and PropertyGuru are Singapore property-listing portals; v4.1 planned to offer them an embeddable score widget as a distribution channel. That is cut — no embed product, no API for third parties, no commercial relationships. Anyone, including agents, may use the free website like any other visitor; researchers may use the open dataset under its licence.)*

---

## 4. Core Model Decisions (LOCKED from v4.1)

**D1 — Real paths, not corridors.** Route on a pedestrian network assembled from Overture/OSM footways enriched with LTA Covered Linkway, pedestrian overhead bridges, and underpasses (`covered` / `underground` edge attributes; the Citymapper OSM import of LTA linkway data provides a head start, cross-validated against the raw LTA layer).

**D2 — Two routes per origin–destination pair.** (a) shortest path; (b) *best-sheltered path* within a ≤ 25% detour budget, generated by penalizing uncovered edges on the project's own routed graph (router amendment, §15) and accepted by post-filter (length ≤ 1.25× shortest). Shelter/heat scored on the sheltered path; access distance on the shortest. **v4.2 framing:** the rendered route is *evidence for the score* — it exists to show where shelter breaks, not to compete with Undercover's live navigation.

**D3 — Node set, not nearest exit.** Every exit of the nearest MRT/LRT station, plus the second-nearest station if within 1.2× the distance of the first, plus bus stops within 250 m routed distance meeting the §7 frequency threshold. Transit Access uses the best node; shelter/heat computed along that node's chosen path.

**D4 — Static serving, no JIT.** All artifacts precomputed. Unknown/new postals return `NOT_YET_SCORED` until the next batch run.

---

## 5. Data Sources

| Dataset | Source | Licence | Use |
|---|---|---|---|
| Covered Linkway | LTA DataMall (geospatial) | Singapore Open Data Licence (attribution) | Sheltered edges |
| Pedestrian Overhead Bridge / Underpass | LTA DataMall | SODL | Sheltered/underground edges; grade-separated crossings |
| Bus Stops / Services / Routes | LTA DataMall | SODL + API ToS | Stops geometry; AM-peak frequency ranges (midpoints, bounds retained) |
| MRT/LRT Station Exits | data.gov.sg | SODL | Destination nodes |
| Traffic Signal Aspects | data.gov.sg | SODL | Crossing Friction (`TYP_NAM` pedestrian filter) |
| Lamp Posts | data.gov.sg | SODL | Night Mode overlay only |
| Postal / Building points | data.gov.sg | SODL | Centroid source of record |
| Pedestrian network | OSM / Overture | ODbL / permissive | Routable graph |
| **Building footprints + heights (`building:levels`/height)** *(new, Phase 4)* | OSM / Overture | ODbL / permissive | **Shadow-exposure term in Heat Comfort** — the Undercover-validated approach, built independently |
| NParks tree data *(Phase 4)* | trees.sg / data.gov.sg | Per NParks terms | Canopy term in Heat Comfort |
| OneMap (search; walk-routing) | SLA OneMap API | OneMap ToS; credentials server-side | Geocoding via proxy; routing for **validation only** |
| Real-time weather / UV *(Phase 4)* | data.gov.sg real-time APIs | Open, keyless | Live Comfort layer, **called client-side** (no server cost) |

**Ingestion rule (unchanged):** no date-stamped URLs in code; fetch latest, SHA-256 content-hash each payload; the hash is the re-computation trigger and the provenance record. Raw payloads stored immutably (local `raw/` archive, versioned by hash; optionally mirrored to the GitHub repo via Git LFS or Releases for backup — storage only, no Actions).

**Explicit non-source:** Undercover itself. It is a Limited Release prototype with **no public API, no downloadable data, and no open-source repo** — there is nothing to consume, and its inputs (LTA + OSM) are the same open datasets above, available to us directly.

---

## 6. Pipeline (Local Native Batch)

```
[local machine, native Python 3.12 via uv]
check   →  fetch latest listings, hash, diff vs manifest      (run.py check)
ingest  →  download changed sources, store raw immutably       (run.py ingest)
network →  build graph: reproject 3414, conflate covered/underground, QA
route   →  python-igraph on the conflated graph: 140k postals × node sets, dual weights
score   →  §7 formulas + config weights
export  →  web/public/data/: scores/{area}.json, geom/{area}.json, manifest.json
validate→  golden set, distribution drift, OneMap sample
publish →  vercel deploy --prod                                (run.py publish)
```

- **Runner:** the owner's Windows 11 machine, native Python (uv-managed). Run `python run.py check` on a personal reminder (monthly is plenty — LTA geospatial refreshes ~quarterly); full rebuild only when hashes change. **No GitHub Actions anywhere in the critical path** (the repo lives on GitHub for code hosting and history only).
- **No message broker, no cloud cron.** One idempotent local pipeline; the manifest-pointer publish is atomic (a deploy either completes or the previous deployment keeps serving).
- **Scale envelope:** 140k origins × ~10 OD pairs, embarrassingly parallel; target full-island batch < 3 h on an 8-core desktop. Dev iterations run per-planning-area.
- **Publish path:** artifacts are written into the web app's `public/data/` and shipped with `vercel deploy --prod`. A monthly static deploy is trivially within Hobby build allowances. (If the artifact set ever presses against Vercel's source-upload limit, shard further or move only the largest files to Vercel Blob — see §8.)

---

## 7. Scoring Methodology (LOCKED weights; config-versioned)

| Sub-score | Weight | Definition |
|---|---|---|
| **Transit Access** | 35% | Routed distance to best node. 100 pts ≤ 400 m; linear to 40 at 800 m; 0 at 1,200 m. Anchors: LTA Walk2Ride 400 m sheltered radius; ~800 m ≈ 10-minute walk. Bus interchanges: 200 m full-credit anchor. |
| **Bus Connectivity** | 20% | Expected-wait: combined headway `H = 1/Σ(1/hᵢ)` (hᵢ = AM-peak range midpoint) over qualifying services at stops ≤ 250 m routed; `W = H/2`; 100 pts at W ≤ 2 min, 0 at ≥ 15 min. *(Initial breakpoints; §12 calibration gate owns final values.)* |
| **Rain Shelter** | 25% | Covered-length ratio along the sheltered path, with each contiguous **exposure gap** (length + location) emitted — the explainability payload. |
| **Heat Comfort** | 15% | Weighted shade along path: underground 1.0, covered 0.7, tree canopy 0.5 *(Phase 4)*, **shadow-shaded 0.4** *(Phase 4: OSM building heights + solar position at representative hours, e.g., mean of 09:00 / 13:00 / 17:00 exposure — a static index term, deliberately not Undercover's live time-of-day query)*, exposed 0. Until Phase 4, computed covered-only and labelled *provisional*. |
| **Crossing Friction** | 5% | At-grade signalized crossings on the sheltered path (DBSCAN eps 20 m, minpts 2, `TYP_NAM` pedestrian filter); 100 − 20 each, floor 0; grade-separated exempt. *(Initial penalty; calibration gate owns it.)* |
| **Night Safety** | — | Overlay only (near-uniform lighting carries no rank-order signal). |

Aggregation, kill rule, and calibration-constant policy unchanged from v4.1.

---

## 8. Outputs & Serving (Vercel Hobby)

**Static artifacts, shipped inside the Vercel deployment:**

- `/data/scores/{planning_area}.json` (~55 files) — all score records for the area; the frontend fetches exactly one per lookup. Gzipped, immutable, hash-versioned filenames.
- `/data/geom/{planning_area}.json` (~55 files) — polyline-encoded route geometries + exposure gaps, fetched only when a postal is selected. Keeps per-view transfer in the hundreds of KB.
- `/data/manifest.json` — provenance: source hashes, code tag, `generated_at`.
- **Open dataset:** the same files, plus a documented bundle in the GitHub repo (Releases), published under attribution (§11). Openness remains the anti-scrape strategy — there is nothing to exfiltrate.

**No island-wide map tiles at MVP.** The map shows the OneMap basemap (their servers) plus the selected postal's route/gaps rendered client-side from GeoJSON. The island-wide shelter-union overlay ("Rain Mode" full-map view) becomes **Phase 2**: a generalized PMTiles file (target well under 1 GB) served from **Vercel Blob free tier** (1 GB storage / 10 GB transfer per month; supports the HTTP range requests PMTiles needs). If the Blob transfer cap is hit, the overlay feature-flags off until the month resets — the core product is unaffected, and nothing bills.

**Serverless surface (the only one): `/api/onemap-search`** — a Vercel function proxying OneMap geocoding, caching the 3-day token, holding credentials server-side, with a light per-IP throttle to protect the OneMap quota. Comfortably inside Hobby's function limits at this project's traffic. *(Deleted in v4.2: `GET /api/shiok/{postal}` — with no B2B consumers, the frontend reads the static JSON directly and no API exists to defend.)*

**Fail-safe economics:** every Vercel Hobby cap (bandwidth, invocations, Blob transfer) **pauses on exhaustion rather than billing**. For a $0-budget civic project this is a feature: the worst possible traffic day costs nothing.

---

## 9. Frontend & UX

- **Stack:** Next.js (static-first) + MapLibre GL JS on Vercel; OneMap basemap tiles with required attribution.
- **Score card:** total + sub-score bars + top-2 plain-language reasons ("Low because: 180 m exposed near the canal; 1 uncovered crossing").
- **Route view (evidence, not navigation):** the sheltered and shortest paths with the detour delta; exposed segments marked by **pattern + colour, never colour alone**. Copy explicitly frames it as "why your score is what it is." A small "Planning a trip right now? Try Undercover" pointer is acceptable and honest.
- **Compare mode:** two postals side by side — the core resident/house-hunter job.
- **Modes:** Rain Mode (Phase 2 overlay), Night Mode (lamppost overlay).
- **Accessibility (launch-gating):** WCAG 2.1 AA; keyboard navigation; text equivalent for every map answer; reduced-motion respected.

---

## 10. Non-Functional Requirements

- **Cost:** **US$0/month standing**, no payment method on file with any provider. Compute cost = owner's electricity. Only optional spend: a custom domain (~US$10–15/yr) vs the free `*.vercel.app` subdomain.
- **Availability:** Vercel CDN for static assets; last successful deployment keeps serving during any pipeline failure. No formal SLA claimed — this is a free civic tool and says so.
- **Failure modes:** free-tier caps pause features (never bill); the OneMap proxy degrades to "search unavailable, paste a postal code" if throttled.
- **Observability:** pipeline run log + drift check (score-distribution shift vs previous run > threshold → hold publish) executed locally before deploy; a free uptime ping on the homepage; zero-score anomaly check with the islands allowlist (Sentosa interior, Jurong Island, military areas).
- **Security:** OneMap credentials only in Vercel env vars; no other secrets exist; pinned dependency lockfiles (`uv.lock`, `package-lock.json`) for the local pipeline.
- **Privacy (PDPA):** static serving — postal lookups never hit an application server; no accounts, no query logs, aggregate page analytics at most. Postal codes are building-level identifiers; the cleanest compliance is to store nothing, so we store nothing.

---

## 11. Legal, Licensing & Non-Commercial Policy (Launch-Gating)

1. **Non-commercial declaration:** S.H.I.O.K. carries no ads, no donations, no paid features, no sponsorships, and enters no commercial agreements. This is a personal/civic open-data project. **This is also precisely what makes Vercel Hobby hosting compliant** (Hobby is restricted to non-commercial projects). **Revisit clause:** if commercialization is ever reconsidered, it requires — before any launch — a move to Vercel Pro *and* a licensing review (ODbL share-alike, LTA/OneMap terms). Until then that clause stays dormant.
2. **Attribution:** Singapore Open Data Licence attribution for all LTA DataMall / data.gov.sg datasets; OneMap/SLA attribution for basemap and search — in-product footer and in the open dataset's README.
3. **ODbL (OSM/Overture-OSM):** published *scores* treated as facts/produced work; published *route geometries* offered under ODbL with attribution. As a non-commercial open project this posture is comfortable; the attribution matrix (dataset → obligation → placement) remains a Phase 0 deliverable and launch blocker.
4. **NParks terms** reviewed before Phase 4 canopy ingestion.

---

## 12. Validation & Calibration

1. **Network QA:** connectivity, dangling edges, covered-tag coverage per planning area reconciled against raw LTA linkway totals.
2. **Routing validation (quantitative gate):** 2,000-postal stratified sample vs **OneMap walk-routing**; thresholds per §2; per-area fallback to OneMap-routed paths if OSM coverage fails tolerance.
3. **Typology audit (50 addresses)** across integrated developments, bus-first HDB estates, canal-adjacent blocks, new BTOs, landed enclaves — **now including a qualitative Undercover spot-check**: does S.H.I.O.K.'s best-sheltered path broadly agree with Undercover's sheltered/shaded route for the same OD pair? (Manual — Undercover has no API. Divergences are logged and investigated, not auto-failed: the tools optimize different objectives.)
4. **Sensitivity analysis** + kill rule; settles the §7 calibration constants.
5. **Golden-set regression** on every pipeline run; publishes blocked on regression.

---

## 13. Positioning & Landscape (New in v4.2)

| | **S.H.I.O.K. Index** | **Undercover (OGP prototype)** |
|---|---|---|
| Question answered | "How comfortable is *this address*, structurally?" | "How do I get A→B shaded/sheltered *right now*?" |
| Unit | Per-postal score, stable, comparable | Per-trip route, time-of-day dependent |
| Decomposition | 5 explainable sub-scores + exposure gaps | Single shade % per route |
| Bus connectivity | Modelled (expected wait) | Absent |
| Crossing friction | Modelled | Absent |
| Dynamic sun shadows | Static representative-hours term (Phase 4) | Live ray-traced, per query |
| Data out | **Open dataset + provenance manifest** | None (no API, no download, closed source) |
| Status | Independent civic project, $0 infra | Government Limited Release prototype; may change or be discontinued |

**Stance:** complement, not compete. The "first sheltered-walkability product" claim is retired; Undercover's existence *validates* demand for shelter-aware pedestrian tooling. S.H.I.O.K. does not build navigation, and happily points trip-planners to Undercover. **Optional, zero-cost action:** a friendly note to undercover@open.gov.sg — OGP invites collaboration, and an open per-address comfort dataset is exactly the "urban planning data layer" their page aspires to; a civic non-commercial project is a natural partner. Low effort, pure option value.

**Known shared limitation (unchanged, documented honestly):** private indoor links — void decks, malls, basements — are absent from public data for both projects. Undercover's void-deck narrative is not backed by a published dataset. S.H.I.O.K. keeps its "indoor connection likely" badge, does not over-invest here, and holds crowdsourced corrections as a Phase 4+ stretch idea.

---

## 14. Roadmap

| Phase | Timing | Deliverables | Exit criteria |
|---|---|---|---|
| **0 — Foundations** | This week | Repo + native uv-locked Python env; fetch-and-hash `run.py check` working against all sources; attribution matrix drafted; Vercel Hobby project created (frontend hello-world + OneMap proxy live) | Hashes stable across two fetches; proxy returns geocodes; $0 verified (no card on file) |
| **1 — Network & router (prototype)** | Wk 1–2 | Pedestrian network (OSM/Overture + LTA layers, EPSG:3414); in-graph dual-weight routing (python-igraph); **3-planning-area prototype deployed to Vercel** with score card + route-evidence view | Prototype areas pass OneMap validation thresholds; per-view transfer ≤ ~500 KB |
| **2 — Full island** | Wk 3–4 | Scoring engine + config; sensitivity analysis; full 140k batch; ~110 artifact files exported; compare mode | Full batch < 3 h; kill rule applied; golden set green; deployment within Hobby limits |
| **3 — Hardening & public beta** | Wk 5–6 | NFRs (§10), a11y, attribution, drift gate, open-dataset bundle on GitHub Releases; "Data as of" banner | §12 gates green; licensing checklist done |
| **4 — Depth (stretch)** | Post-MVP | NParks canopy + building-shadow term → real Heat Comfort; Rain Mode overlay (PMTiles in Vercel Blob); Live Comfort strip (client-side data.gov.sg weather/UV + bus arrivals); crowdsourced corrections exploration; OGP hello email | Each behind a feature flag; $0 preserved |

---

## 15. Decision History

- **v4.1 (LOCKED, cross-review Round 1):** Option A killed; Valhalla; node sets; 25% detour + split scoring; Walk2Ride two-anchor calibration; Night Safety to overlay; cron-not-broker; 6-week gated plan; open publication; ODbL scores/geometry split.
- **v4.2 (owner decisions + Undercover review, 2026-07-25):** B2B and convenience API deleted; non-commercial policy adopted (unlocks Vercel Hobby); hosting = Vercel Hobby + local Docker, GitHub Actions and Cloudflare removed; route reframed as score evidence; shadow-exposure term and Undercover qualitative validation adopted; positioning rewritten as complement to Undercover; MVP ships without island-wide tiles (Phase-2 Blob overlay).
- **Router amendment (2026-07-25, owner sign-off):** routing moved from Valhalla to the project's own conflated graph (python-igraph, dual weight columns); OneMap remains the sole external validation baseline. Rationale: scoring needs per-edge attribution on our graph; an external engine adds geometry→edge map-matching and PBF write-back of synthesized linkway edges (see ENGINEERING_REVIEW.md §1).
- **Windows-native amendment (2026-07-25, owner sign-off):** no WSL, no Docker (Docker Desktop requires a WSL2 backend); pipeline runs on native Windows Python 3.12 via uv; `make` replaced by cross-platform `run.py`. Feasible because the router amendment removed the only container dependency.
- **Still open, by design:** final hosting fine-tuning and the prototype execution plan — next working session. Calibration constants owned by the §12 gate, as before.
