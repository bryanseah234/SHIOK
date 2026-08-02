# Mayflower Route QA Summary

Bundle: `generated_20260803_safe_mayflower_560234_targeted`

## Route Scores
- `560231`: state `SCORED`, total `96.9`
  - best_transit: `SCORED`, total `96.9`, node `Mayflower Sec Sch`, distance `193.8`, covered `0.921`
  - mrt_lrt: `SCORED`, total `70.8`, node `MAYFLOWER MRT STATION Exit 5`, distance `440.1`, covered `0.302`
  - bus: `SCORED`, total `96.9`, node `Mayflower Sec Sch`, distance `193.8`, covered `0.921`
- `560234`: state `SCORED`, total `96.6`
  - best_transit: `SCORED`, total `96.6`, node `Mayflower Sec Sch`, distance `400.6`, covered `0.914`
  - mrt_lrt: `SCORED`, total `70.2`, node `MAYFLOWER MRT STATION Exit 5`, distance `649.2`, covered `0.498`
  - bus: `SCORED`, total `96.6`, node `Mayflower Sec Sch`, distance `400.6`, covered `0.914`
- `560225`: state `SCORED`, total `85.3`
  - best_transit: `SCORED`, total `85.3`, node `Mayflower Sec Sch`, distance `433.7`, covered `0.704`
  - mrt_lrt: `SCORED`, total `59.7`, node `MAYFLOWER MRT STATION Exit 5`, distance `646.9`, covered `0.363`
  - bus: `SCORED`, total `85.3`, node `Mayflower Sec Sch`, distance `433.7`, covered `0.704`

## Active MRT Gap Signals
- `560225`: MRT covered `0.363`, best covered `0.704`, largest MRT exposed gap `293` m, signal `True`
- `560231`: MRT covered `0.302`, best covered `0.921`, largest MRT exposed gap `292` m, signal `True`
- `560234`: MRT covered `0.498`, best covered `0.914`, largest MRT exposed gap `292` m, signal `True`

## Active Route Segment Sources
- `560225`
  - best_transit: exposed `128` m, covered `305` m, sources `{'bridge_underpass': 67.0, 'exposed': 128.2, 'inferred_hdb_void_deck': 226.5, 'osm_covered': 11.9}`, largest gap `106` m
  - mrt_lrt: exposed `412` m, covered `235` m, sources `{'bridge_underpass': 8.4, 'exposed': 412.0, 'inferred_hdb_void_deck': 226.5}`, largest gap `293` m
  - bus: exposed `128` m, covered `305` m, sources `{'bridge_underpass': 67.0, 'exposed': 128.2, 'inferred_hdb_void_deck': 226.5, 'osm_covered': 11.9}`, largest gap `106` m
- `560231`
  - best_transit: exposed `15.3` m, covered `178` m, sources `{'audited_shelter_correction': 71.9, 'exposed': 15.3, 'inferred_hdb_void_deck': 94.6, 'osm_covered': 11.9}`, largest gap `9.1` m
  - mrt_lrt: exposed `307` m, covered `133` m, sources `{'bridge_underpass': 8.4, 'exposed': 307.2, 'inferred_hdb_void_deck': 124.6}`, largest gap `292` m
  - bus: exposed `15.3` m, covered `178` m, sources `{'audited_shelter_correction': 71.9, 'exposed': 15.3, 'inferred_hdb_void_deck': 94.6, 'osm_covered': 11.9}`, largest gap `9.1` m
- `560234`
  - best_transit: exposed `34.4` m, covered `366` m, sources `{'audited_shelter_correction': 56.1, 'exposed': 34.4, 'inferred_hdb_void_deck': 232.9, 'osm_covered': 77.2}`, largest gap `12.6` m
  - mrt_lrt: exposed `326` m, covered `323` m, sources `{'audited_shelter_correction': 16.5, 'bridge_underpass': 8.4, 'exposed': 326.3, 'inferred_hdb_void_deck': 232.9, 'osm_covered': 65.3}`, largest gap `292` m
  - bus: exposed `34.4` m, covered `366` m, sources `{'audited_shelter_correction': 56.1, 'exposed': 34.4, 'inferred_hdb_void_deck': 232.9, 'osm_covered': 77.2}`, largest gap `12.6` m

## Feedback Segment Classes
- `560225`: `{'hdb_void_deck_component_gap': 5, 'covered_evidence_nearby_check_connectivity_or_snap': 6, 'hdb_void_deck_evidence_nearby_check_connectivity': 2}`
- `560231`: `{'covered_evidence_nearby_check_connectivity_or_snap': 4, 'hdb_void_deck_component_gap': 3, 'bridge_underpass_evidence_nearby_check_endpoint_snap': 1}`

## Connector Candidates
- candidate count: `8`
- promotion statuses: `{'review_ready_not_scoring': 5, 'blocked_insufficient_source_overlap_not_scoring': 3}`
- classifications: `{'hdb_source_overlap_review': 4, 'insufficient_source_overlap': 3, 'covered_source_overlap_review': 1}`

## Candidate Details
- `560225`
  - `feedback-560225-segment-0-insufficient-source-overlap`: segment `0`, label `void_deck`, length `64` m, status `blocked_insufficient_source_overlap_not_scoring`, class `insufficient_source_overlap`, covered overlap `0.309`, HDB overlap `0.309`, OSM shelter overlap `0`, official shelter overlap `0`
  - `feedback-560225-segment-2-hdb-source-overlap-review`: segment `2`, label `sheltered`, length `16.1` m, status `review_ready_not_scoring`, class `hdb_source_overlap_review`, covered overlap `1`, HDB overlap `1`, OSM shelter overlap `0`, official shelter overlap `0`
  - `feedback-560225-segment-7-hdb-source-overlap-review`: segment `7`, label `sheltered`, length `16.5` m, status `review_ready_not_scoring`, class `hdb_source_overlap_review`, covered overlap `1`, HDB overlap `1`, OSM shelter overlap `0.485`, official shelter overlap `0`
  - `feedback-560225-segment-8-covered-source-overlap-review`: segment `8`, label `covered_bridge`, length `37` m, status `review_ready_not_scoring`, class `covered_source_overlap_review`, covered overlap `1`, HDB overlap `0.216`, OSM shelter overlap `1`, official shelter overlap `0`
  - `feedback-560225-segment-11-insufficient-source-overlap`: segment `11`, label `void_deck`, length `129` m, status `blocked_insufficient_source_overlap_not_scoring`, class `insufficient_source_overlap`, covered overlap `0.105`, HDB overlap `0.105`, OSM shelter overlap `0`, official shelter overlap `0`
- `560231`
  - `feedback-560231-segment-1-hdb-source-overlap-review`: segment `1`, label `void_deck`, length `92.9` m, status `review_ready_not_scoring`, class `hdb_source_overlap_review`, covered overlap `1`, HDB overlap `1`, OSM shelter overlap `0`, official shelter overlap `0`
  - `feedback-560231-segment-2-hdb-source-overlap-review`: segment `2`, label `sheltered`, length `37.7` m, status `review_ready_not_scoring`, class `hdb_source_overlap_review`, covered overlap `1`, HDB overlap `1`, OSM shelter overlap `0.388`, official shelter overlap `0`
  - `feedback-560231-segment-6-insufficient-source-overlap`: segment `6`, label `void_deck`, length `129` m, status `blocked_insufficient_source_overlap_not_scoring`, class `insufficient_source_overlap`, covered overlap `0.105`, HDB overlap `0.105`, OSM shelter overlap `0`, official shelter overlap `0`

## Conclusion
- score override used: `False`
- approved source-backed corrections: `5`
- ready for owner review: `0`
- blocked without more source evidence: `3`
