# Comfort Modes and Data QA Backlog

Status: product/data backlog. Keep this file honest: do not mark any mode or
data source as production-real until the source is ingested, hashed, tested, and
visible in artifact provenance.

## Fast Recompute Rule

Route metrics should be computed once per postal/node set, then reused:

- shortest distance
- Shiokest distance
- covered length and ratio
- exposed gaps
- crossings
- bus stops and service headways
- future shade/leaf/building-shadow exposure

User modes must be score permutations over those metrics, not separate route
runs. This keeps Rain + AM, Rain + PM, Sunny + AM, Sunny + PM, and midday
views cheap in the frontend and avoids re-running the island batch for UI-only
weight changes.

## Mode Matrix

MVP-ready client modes:

- Balanced: current PRD weights.
- Rain + AM: higher rain shelter and bus weight.
- Rain + PM: same scoring shape as Rain + AM until PM bus headway parsing is
  wired.
- Sunny + AM: higher heat comfort weight.
- Sunny + PM: same scoring shape as Sunny + AM until PM bus headway parsing is
  wired.
- Sunny midday: maximum heat comfort weight.

Honesty labels:

- Bus is scheduled frequency, not historical arrival reliability.
- Heat is provisional until NParks Leaf Area Index and/or shadow modeling lands.
- Shelter is rain protection. Tree foliage is shade, not rain shelter.

## Bus as Transit

Current target:

- Include bus stops as transit candidates when they are within 300 m direct
  radius and have scheduled service headway data.
- Reuse the combined postal-to-node route result to compute bus connectivity.
- Keep bus quality dependent on expected wait/service coverage so a weak nearby
  bus stop does not score like an MRT station.

Later refinement:

- Parse DataMall AM peak, PM peak, and off-peak service frequencies separately.
- Add route evidence for bus stop side-of-road access where graph data allows.
- Keep direct-line fallback visible only as "nearby bus stop"; do not invent a
  sheltered routed walk if the graph cannot route it.

## Actual Bus Arrivals

DataMall Bus Arrival is live/current data. It does not give historical
reliability by itself.

To build actual arrival reliability:

- Run a local collector on this Windows machine.
- Poll selected bus stops at a conservative interval.
- Store timestamped arrivals in local Parquet or SQLite.
- Aggregate by stop, service, direction, day type, and time band.
- Publish only aggregated static artifacts, never a runtime database.

This is not an MVP blocker. It needs days or weeks of collection before it is
product-trustworthy.

## Shade and Leaf Coverage

Candidate official source:

- NParks Leaf Area Index on data.gov.sg.

Expected use:

- Treat Leaf Area Index as shade/heat evidence.
- Do not count trees as rain shelter.
- Combine future shade evidence with covered paths and, later, building shadow
  by time of day.

## Missing Network Feature Checklist

Keep these in algorithm/data QA until each is source-backed and regression
tested:

- HDB void decks.
- HDB block-to-block sheltered precinct paths.
- Covered linkway-to-footway connectors.
- Covered overpass, bridge, and underpass endpoint snapping.
- MRT exit snap quality.
- Bus stop side-of-road access.
- Public indoor links and mall links where source-backed.
- Arcades and covered public walkways.
- PCN and park paths.
- Stairs, ramps, lifts, and escalators.
- Barriers, gates, private access, and construction closures.
- Tree canopy / Leaf Area Index.
- Building shadow by time of day.

## Human Feedback Loop

Add a static-first "Suggest better route" flow:

- User draws the route they actually walk.
- User labels each segment: sheltered, void deck, bridge, underpass, exposed, or
  blocked.
- App exports copyable JSON with postal, destination, waypoints, segment labels,
  and user note.
- Treat the submission as QA evidence only.
- Promote it only through a general model fix or audited correction layer.

No postal-specific score override.
