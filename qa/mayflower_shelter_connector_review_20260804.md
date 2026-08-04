# Mayflower Shelter Connector Review — Scoping

Date: 2026-08-04
Status: Persistent open item since 2026-08-01 (docs/COMFORT_MODES_AND_DATA_QA.md)

## Symptom

For postal `560231` (and to a lesser degree `560234`), the MRT/LRT-only route to
Mayflower MRT Exit 5 reports **425.9 m at 31.2% covered/sheltered**, which
contradicts user expectation that the walk is largely under HDB void-decks and
covered walkways.

Best Transit mode does not surface this because it picks a closer bus stop
(`Opp Mayflower Sec Sch` at 128.1 m for `560231`, `Mayflower Sec Sch` at
332.4 m for `560234`). The false-negative is therefore only visible when the
user forces MRT/LRT mode.

## Root cause (evidence in existing QA)

The user-marked evidence route breaks into three graph gaps against the
project's pedestrian network:

| Segment | Length | Category |
|---|---|---|
| 1 | 92.9 m | HDB component gap |
| 2 | 37.7 m | HDB component gap |
| 6 | 128.7 m | HDB component gap, only 10.5% HDB/covered source overlap |

Segment 6 is the dominant one. Source: `qa/route_feedback_component_gap_source_audit_amk_20260801.json`.

## What has been ruled out

- Sheltered-weight tuning: 2026-08-01 lambda×detour sweep (λ∈{2,5,10,25}, detour∈{1.25,1.5,2.0,3.0}) — Exit 5 stays 425.9 m / 31.2% for every combination. Not a scoring policy issue.
- Broader OSM covered-value extraction: promoted `covered=building_arcade`, `covered=shelter`, `covered=roof`, `covered=booth`, `covered=canopy` into `pipeline/config/osm_tags.yaml`, rebuilt the island network — segment 6 remains blocked at 10.5% HDB/covered overlap.
- Bellingcat, OpenInfraMap, Overpass — do not solve Mayflower. These are OSM-derived and share the same gap.
- NParks LAI / greenery shade data — not routing-graph geometry.

Documented in decisions.md 2026-08-01 and COMFORT_MODES_AND_DATA_QA.md §"AMK Mayflower connector work".

## Why compare-targeted keeps blocking

`qa/compare_eligible_selector_mayflower_amk_20260803.json`: promoting the AMK
comparator improves `560234` but regresses `560225`, `560700`, and `560710`
under the score-drop tolerance. Only `560234` was safe-promotable; that fix
shipped in `generated_20260803_safe_mayflower_560234_targeted` (the current
active bundle). `560231` and `560710` remain unsolved.

`qa/candidate_audit_mayflower_amk_blockers_20260803.json` shows for `560710`
that a nearby `Aft Ang Mo Kio Int` bus stop at 92.9 m / 82.4% covered is
present and ranks #2, but the current winner `Bef Al-Muttaqin Mque` at
476.3 m / 98.4% covered / 95.4 total is picked because the eligibility filter
lets the higher-coverage/farther option win. This is a scoring policy tension,
not a graph gap for `560710`.

## What the fix needs

The 128.7 m HDB gap under `560231` → Mayflower MRT Exit 5 is the only remaining
`insufficient_source_overlap` in the Mayflower bundle. Fixing it requires one
of:

1. **Ground-truth annotation**: someone walks / satellite-images the specific
   HDB void-deck corridor and adds a source-backed connector into
   `data/audited_shelter_corrections.geojson` with an approval record via
   `scripts/promote_audited_shelter_correction.py --approve
   feedback-560231-segment-6-hdb-source-overlap-review --reviewer <owner>
   --evidence-note "…"`.
2. **SLA HDB Existing Building improvements**: if a future SLA/HDB dataset
   raises the source overlap for segment 6 above the 10.5% threshold on its
   own, the connector auto-promotes without hand annotation.
3. **Policy change**: lower the source-overlap threshold below 10.5%. Not
   recommended — the threshold defends against speculative connectors.

## Recommendation

- **Not fixable as an agent-only task.** All auto-detection paths have been
  exhausted. The next step is human-in-the-loop source evidence for segment 6.
- **Not a launch blocker.** Best Transit mode masks the false-negative in the
  default view, and the OneMap walk validation gate for `560231` is not the
  dominant contributor to overall p95.
- **Log this scoping doc** so the next agent session or human review starts
  from here rather than re-discovering.

## Related files

- `data/audited_shelter_corrections.geojson` — the approved connectors set (12 features today)
- `qa/route_feedback_component_gap_source_audit_amk_20260801.json` — segment-by-segment audit
- `qa/oriented_mayflower_amk_geometry_audit_20260803.json` — post-orient geometry check (segment gaps now 0.0m in exported bundle)
- `qa/candidate_audit_mayflower_amk_blockers_20260803.json` — candidate scoring debug
- `scripts/promote_audited_shelter_correction.py` — human-approval CLI
