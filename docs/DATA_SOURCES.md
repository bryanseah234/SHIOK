# DATA_SOURCES.md — endpoints, auth, formats, gotchas

Rule zero: never hardcode a date-stamped file URL. Discover, download, hash (see BUILD_PLAN T0.3).
All geometry ops in EPSG:3414; sources arrive in WGS84 (EPSG:4326) or SVY21 depending on layer — always check, never assume.
DataMall zip auth is unverified: the fetcher tries unauthenticated first, falls back to the `AccountKey` header (settled empirically in T0.3, recorded in `docs/decisions.md`).

## Auth the OWNER must set up first (agents cannot register accounts)

| Credential | Where to get it | Used for |
|---|---|---|
| `LTA_DATAMALL_ACCOUNT_KEY` | datamall.lta.gov.sg → "Request for API Access" (free) | All DataMall API + geospatial downloads (send as `AccountKey` header) |
| `ONEMAP_EMAIL` / `ONEMAP_PASSWORD` | onemap.gov.sg → register (free) | Token via `POST https://www.onemap.gov.sg/api/auth/post/getToken` (expires ~3 days) — search proxy + validation routing |
| Vercel account + `vercel login` | vercel.com (Hobby, free, no card) | Frontend + proxy deploys |

## Upstream datasets

| # | Dataset | Where / how | Format | Gotchas |
|---|---|---|---|---|
| 1 | Covered Linkway | LTA DataMall → Geospatial ("Static Datasets") — discover current zip via the DataMall geospatial listing page; filename carries a date that CHANGES quarterly | zipped SHP | Polygons, not lines: derive centerlines or conflate by buffer-overlap onto OSM footways (T1.1). SVY21 native. |
| 2 | Pedestrian Overhead Bridge / Underpass | Same DataMall geospatial listing | zipped SHP | Note upstream filename historically misspells "Pedestrain". Split overhead vs underpass attribute → `underground` flag; these also define grade-separated crossings for Crossing Friction exemption. |
| 3 | Bus Stops | DataMall geospatial zip AND API `ltaodataservice/BusStops` | SHP / JSON (paged, 500/skip) | Join key = `BusStopCode`. API pages via `$skip`. |
| 4 | Bus Services | API `ltaodataservice/BusServices` | JSON paged | `AM_Peak_Freq` is a RANGE STRING like "06-08" (minutes) — parse to midpoint, keep both bounds; values can be "-" (no service window) → exclude. One row per service+direction. |
| 5 | Bus Routes | API `ltaodataservice/BusRoutes` | JSON paged (~26k rows) | Maps ServiceNo→ordered BusStopCodes; used to attach services to stops. |
| 6 | MRT/LRT Station Exits | data.gov.sg dataset "LTA MRT Station Exit" — resolve via dataset page / API (`api-open.data.gov.sg` initiate-download), don't pin resource ids | GeoJSON | Exit points ≠ station centroids — exits are the destination nodes (PRD D3). Station name spellings vary; normalize. |
| 7 | Traffic Signal Aspects | data.gov.sg ("Traffic Signal Aspect") | GeoJSON/SHP | Filter `TYP_NAM` to pedestrian-relevant classes (e.g., contains "PEDESTRIAN"/"GREEN MAN" — enumerate actual values at ingest and log the distinct set before filtering). Cluster with DBSCAN eps=20 m, minpts=2 in 3414. |
| 8 | Lamp Posts | data.gov.sg ("Lamp Post") | GeoJSON | Overlay only. Large point set — simplify for display. |
| 9 | Postal / building points | data.gov.sg HDB/building datasets + one-time bulk OneMap geocode (BUILD_PLAN T0.6) | CSV/GeoJSON | One postal = one building in SG. No single open file covers all ~140k postals (landed/private gaps) — the T0.6 geocode cache in `raw/geocode/` is the real source of record. Dedupe. |
| 10 | OSM extract | Geofabrik `malaysia-singapore-brunei` PBF (clip to SG) or BBBike "Singapore" extract | PBF | Contains the Citymapper-imported LTA linkways as `covered=*` footways — cross-check against layer 1; keep both signals. ODbL. Read on Windows with pyrosm or QuackOSM (pure-wheel); fall back to pyosmium only if its Windows wheel installs cleanly. |
| 11 | Overture (optional assist) | Overture releases via DuckDB `read_parquet` on their S3 (anonymous) | GeoParquet | Buildings theme has heights for Phase 4 shadow term; transportation theme optional cross-check. |
| 12 | Planning Area boundaries | data.gov.sg — URA *Master Plan 2019 Planning Area Boundary (No Sea)*, id `d_4765db0e87b9c86336792efe8a1f7a66` (GeoJSON ~2 MB); prefer a DMP25 planning-area layer if published (several MP2019 layers marked superseded Jun 2025) | GeoJSON | Partition key for exports/QA + source of the OSM clip polygon (union, buffer ~500 m). |

## OneMap specifics

- Token: `POST /api/auth/post/getToken` with email/password → bearer for authorized endpoints; cache ~71h, refresh on 401.
- Search (proxy): `GET /api/common/elastic/search?searchVal=...&returnGeom=Y&getAddrDetails=Y` — public but proxied anyway to keep one config path and add throttling.
- Walk routing (validation only): `GET /api/public/routingsvc/route?start=lat,lng&end=lat,lng&routeType=walk` with token. Throttle per the T0.4 probe; cache every response by (start,end) hash under `raw/validation/`. Never in the batch path.
- Rate limits: not publicly confirmed — probe in T0.4 (ramp to 429, honor `Retry-After`), set client throttle to ~50% of observed. Budget the one-time 140k geocode (T0.6) at ~10–24 h wall clock, resumable.
- Basemap tiles: OneMap XYZ tiles with required attribution line in the map corner.

## data.gov.sg specifics

- Prefer the official download-initiation API over scraping HTML; store the dataset *page* URL in config, resolve the file at fetch time.
- Real-time weather/UV APIs (Phase 4) are keyless and browser-callable — client-side only.

## Attribution (must ship — see docs/ATTRIBUTION.md)

- LTA DataMall + data.gov.sg layers: Singapore Open Data Licence attribution.
- OneMap: SLA/OneMap attribution on map + search.
- OSM-derived: "© OpenStreetMap contributors" (ODbL); published route geometries are ODbL.
