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
Exits:

- station name
- exit code
- system inferred from station name (`MRT` or `LRT`)
- station exit count

## Not Active Yet

- Live bus arrivals/ETA/load: requires DataMall `AccountKey` behind a Vercel API
  route with caching, or local collection plus published static aggregates.
- MRT/LRT line and direction labels: current MRT/LRT exits source does not carry
  line codes. Needs a legitimate static rail-line/station-code source or an
  audited station mapping.
- MRT/LRT first/last train: not in the current bulk source. Needs an official
  static ingestion path before display.

## Frontend Boundary

The current popup is intentionally static. It must not claim live arrivals unless
the runtime proxy/cache or collected aggregate data contract is implemented.
