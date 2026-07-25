# Attribution matrix — T0.5 deliverable

Complete the Placement column, finalize exact wording against each licence's current text,
and wire the site footer + map-corner components. Launch-gating per PRD §11.

| Dataset / service | Licence / terms | Required attribution (draft wording — verify) | Placement |
|---|---|---|---|
| LTA DataMall — Covered Linkway, Overhead Bridge/Underpass, Bus Stops/Services/Routes | Singapore Open Data Licence + DataMall API ToS | Attribution naming LTA as source under the Singapore Open Data Licence | Site footer + open-dataset README (TODO wire) |
| data.gov.sg — MRT/LRT exits, traffic signals, lamp posts, postal/buildings, MP2019 Planning Area Boundary (URA) | Singapore Open Data Licence | Attribution naming the source agency (LTA / URA) under the Singapore Open Data Licence | Site footer + README (TODO) |
| OneMap / SLA — basemap tiles, search | OneMap API Terms of Service | OneMap/SLA attribution line rendered on the map | Map corner (TODO) |
| OpenStreetMap — pedestrian network (incl. Citymapper covered-linkway import) | ODbL | "© OpenStreetMap contributors"; published route geometries offered under ODbL | Map corner + README + dataset licence note (TODO) |
| Overture Maps — building heights (Phase 4) | Verify per theme at ingest | TBD | — |
| NParks — tree data (Phase 4) | Verify before ingest | TBD | — |

Notes:
- The open dataset bundle (GitHub Releases, Phase 3) needs its own LICENSE/README carrying
  all of the above plus the ODbL notice for geometries.
- Non-commercial declaration (PRD §11) is a product policy, not a licence — restate it in
  the site footer alongside attribution.
