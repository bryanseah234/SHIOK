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
| 9 | Postal / building points | data.gov.sg HDB Existing Building + SLA Dwelling Information + OSM `addr:postcode` + reviewed postal-universe candidates, including ACRA `d_3f960c10fed6145404ca7b821f263b87` and Other-UEN `d_b1d2b840ab9e993570c037b706b39bb8` registered/live entity postals | CSV/GeoJSON/JSON | No authoritative free all-postal-code file has been found. Use `python run.py postal-universe` and `docs/POSTAL_UNIVERSE.md`; OneMap brute-force enumeration remains forbidden. Entity-address postals are candidate coverage evidence, not a delivery-point universe. |
| 10 | OSM extract | Geofabrik `malaysia-singapore-brunei` PBF (clip to SG) or BBBike "Singapore" extract | PBF | Contains the Citymapper-imported LTA linkways as `covered=*` footways — cross-check against layer 1; keep both signals. ODbL. Read on Windows with pyrosm or QuackOSM (pure-wheel); fall back to pyosmium only if its Windows wheel installs cleanly. |
| 11 | Overture (optional assist) | Overture releases via DuckDB `read_parquet` on their S3 (anonymous) | GeoParquet | Buildings theme has heights for Phase 4 shadow term; transportation theme optional cross-check. |
| 12 | Planning Area boundaries | data.gov.sg — URA *Master Plan 2019 Planning Area Boundary (No Sea)*, id `d_4765db0e87b9c86336792efe8a1f7a66` (GeoJSON ~2 MB); prefer a DMP25 planning-area layer if published (several MP2019 layers marked superseded Jun 2025) | GeoJSON | Partition key for exports/QA + source of the OSM clip polygon (union, buffer ~500 m). |
| 13 | NParks Leaf Area Index | data.gov.sg — NParks *Leaf Area Index (LAI)*, id `d_69141275d795e1fe2e496dda7c267d8d` | XLSX | Plant-species LAI table for future shade/heat calibration. This is not spatial tree-canopy geometry and must not be used as rain shelter. |
| 14 | NParks Nature Ways / green corridors | data.gov.sg NParks datasets: Nature Ways `d_af948e3f29cd12bc8b0caea19ae68286`, Park Connector Loop `d_a69ef89737379f231d2ae93fd1c5707f`, NParks Tracks `d_306cc1018cb733346681883ee6d73054`, Heritage Trees `d_644ff187b6d14d6316f47284a4a6c81f` | GeoJSON / mixed | Active heat-only shade proxy. These are spatial green-route/tree proxies, not rain shelter; the current model uses 8 m line buffers, 6 m point buffers, and `heat_comfort.shade_proxy_weight`. |
| 15 | LTA BusArrival v3 | DataMall API `ltaodataservice/v3/BusArrival` | JSON live API | Live ETA/load/location only. Requires `AccountKey`; never call directly from browser. Use Vercel proxy/cache or local collector + static aggregate. |
| 16 | SingPost SGLocate Dataset | SingPost business data solution / SGLocate | licensed dataset / API | Most plausible canonical full-address/postal universe source, but subscription/licensed. Do not use unless owner has rights. API is not a free bulk enumeration path. |

## OneMap specifics

- Token: `POST /api/auth/post/getToken` with email/password → bearer for authorized endpoints; cache ~71h, refresh on 401.
- Search (proxy): `GET /api/common/elastic/search?searchVal=...&returnGeom=Y&getAddrDetails=Y` — public but proxied anyway to keep one config path and add throttling.
- Walk routing (validation only): `GET /api/public/routingsvc/route?start=lat,lng&end=lat,lng&routeType=walk` with token. Throttle per the T0.4 probe; cache every response by (start,end) hash under `raw/validation/`. Never in the batch path.
- Rate limits: not publicly confirmed — probe in T0.4 (ramp to 429, honor `Retry-After`), set client throttle to ~50% of observed. Budget the one-time 140k geocode (T0.6) at ~10–24 h wall clock, resumable.
- Basemap tiles: OneMap XYZ tiles with required attribution line in the map corner.

## data.gov.sg specifics

- Prefer the official download-initiation API over scraping HTML; store the dataset *page* URL in config, resolve the file at fetch time.
- `datagov_polldownload` sources are not always GeoJSON. Preserve the resolved download extension from `Content-Disposition` or the signed URL, e.g. NParks LAI resolves to XLSX.
- Real-time weather/UV APIs (Phase 4) are keyless and browser-callable — client-side only.

## OSM discovery tools policy

- Use the hashed Geofabrik/OSM PBF as the production OSM source. Extra shelter
  classes should be extracted from that local snapshot first so builds remain
  reproducible.
- The reviewed production tag schema lives in `pipeline/config/osm_tags.yaml`.
  Update that file and its tests before changing network-build OSM extraction.
- Use Overpass/Overpass Turbo for bounded QA and query development. If a
  bounded Overpass result becomes production input, store the raw response,
  query text, timestamp, and hash under `raw/` before any network build.
- Bellingcat OSM Search and OpenInfraMap are QA/discovery interfaces over OSM
  data, not production feeds. Do not add runtime or batch dependencies on them.

## Attribution (must ship — see docs/ATTRIBUTION.md)

- LTA DataMall + data.gov.sg layers, including NParks LAI: Singapore Open Data Licence attribution.
- OneMap: SLA/OneMap attribution on map + search.
- OSM-derived: "© OpenStreetMap contributors" (ODbL); published route geometries are ODbL.
