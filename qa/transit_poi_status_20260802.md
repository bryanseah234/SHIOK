# Transit POI Status - 2026-08-02

Pending bundle: `generated_20260801_165500`

## Active Static Map POI Data

Bus stops are exported with static service metadata from DataMall `BusStops`,
`BusServices`, and `BusRoutes`:

- stop code
- stop name
- road name
- service list
- service count
- weekday first bus
- weekday last bus
- AM peak best frequency
- PM peak best frequency

Sample verified from `transit/pois.json`:

- stop `09213`, `15 Scotts`: services `105, 132, 190, 972`, first bus
  `06:03`, last bus `00:31`, AM peak `7.5`, PM peak `5`
- stop `60201`, `18 Woodsville`: 10 services, first bus `05:57`, last bus
  `00:50`

MRT/LRT exits and station centroids are exported from DataMall MRT/LRT Station
Exits, enriched where possible by the official data.gov.sg Train Station Codes
and Chinese Names CSV:

- station name
- exit code
- system inferred from station name (`MRT` or `LRT`)
- station exit count
- station code and line name for stations present in the Train Station Codes
  source

Pending bundle verification after static enrichment:

- 6,011 total transit POI features
- 661 features with station codes
- 160 of 190 MRT/LRT station centroid features with station codes
- sample enriched station: `ADMIRALTY MRT STATION`, `NS10`,
  `North South Line`

## Not Active Yet

- Live bus arrivals/ETA/load: requires DataMall `AccountKey` behind a Vercel API
  route with caching, or local collection plus published static aggregates.
- Complete MRT/LRT line and direction labels: the Train Station Codes CSV is
  official but incomplete/stale for newer TEL stations. Example:
  `MAYFLOWER MRT STATION` remains missing station code and line name in this
  source.
- MRT/LRT first/last train: not in the current bulk source. Needs an official
  static ingestion path before display.

## Frontend Boundary

The current popup is intentionally static. It must not claim live arrivals unless
the runtime proxy/cache or collected aggregate data contract is implemented.
