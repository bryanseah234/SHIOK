# Shade Proxy Status - 2026-08-02

Network QA: `qa/conflation_qa_island.json`

## Current State

- `real_disconnection_count_final`: 0
- `shade_proxy_edge_count`: 64,289
- `shade_proxy_weighted_length_m`: 840,922.1
- Heat model parameter: `heat_comfort.shade_proxy_weight = 0.5`

## Active Heat-Only NParks Spatial Proxies

| Source | Status | Raw features | In-scope features |
|---|---:|---:|---:|
| NParks Heritage Road Green Buffers | loaded | 5 | 5 |
| NParks Heritage Trees | loaded | 255 | 255 |
| NParks Nature Ways | loaded | 56 | 56 |
| NParks Park Connector Loop | loaded | 878 | 878 |
| NParks Tracks | loaded | 15,425 | 15,425 |

## Boundary

These sources affect Heat Comfort only. They are not rain shelter and must not
increase Rain Shelter coverage.

NParks Leaf Area Index is still calibration-only. It is an XLSX plant/species
table, not route-level canopy geometry.

## Remaining Shade Work

- Better route-level canopy geometry, if a legitimate spatial source is found.
- Building-shadow exposure by representative time windows such as AM, noon, and
  PM.
- UI controls for comfort modes can combine weather/time preferences later, but
  the current MVP bundle remains static-first.
