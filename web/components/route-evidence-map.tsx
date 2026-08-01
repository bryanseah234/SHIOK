"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import type maplibregl from "maplibre-gl";
import type { StyleSpecification } from "maplibre-gl";
import { postalGeomToRouteGeoJson } from "../lib/route-geojson";
import localGlyphProtocol from "../lib/local-glyph-protocol";
import type { LineStringFeatureCollection, LineStringFeature, LngLat } from "../lib/route-geojson";
import type { PostalGeom, TransitPoiCollection } from "../lib/types";
import styles from "./route-evidence-map.module.css";

export type RouteDisplayMode = "shiokest" | "shortest" | "both";

export interface RouteMapItem {
  id: string;
  label: string;
  geom: PostalGeom;
  color: string;
}

export interface FeedbackPoint {
  lng: number;
  lat: number;
}

const SINGAPORE_BOUNDS: [[number, number], [number, number]] = [
  [103.55, 1.13],
  [104.13, 1.49],
];

const ONE_MAP_TILE_BOUNDS = [103.596, 1.1443, 104.4309, 1.4835] as [number, number, number, number];

const ONE_MAP_STYLE: StyleSpecification = {
  version: 8,
  glyphs: "glyphs://{fontstack}/{range}",
  sources: {
    onemap: {
      type: "raster",
      tiles: ["https://www.onemap.gov.sg/maps/tiles/Grey_HD/{z}/{x}/{y}.png"],
      tileSize: 128,
      bounds: ONE_MAP_TILE_BOUNDS,
      minzoom: 8,
      maxzoom: 20,
      attribution: "© OneMap, Singapore Land Authority",
    },
  },
  layers: [
    {
      id: "background",
      type: "background",
      paint: {
        "background-color": "#eef0ed",
      },
    },
    {
      id: "onemap",
      type: "raster",
      source: "onemap",
    },
  ],
};

interface PointFeature {
  type: "Feature";
  geometry: {
    type: "Point";
    coordinates: LngLat;
  };
  properties: Record<string, string | number>;
}

interface PointFeatureCollection {
  type: "FeatureCollection";
  features: PointFeature[];
}

type MapFeatureCollection = LineStringFeatureCollection | PointFeatureCollection;
type MapLibreModule = typeof import("maplibre-gl");

let localGlyphProtocolRegistered = false;

async function ensureLocalGlyphProtocol(maplibre: MapLibreModule) {
  if (localGlyphProtocolRegistered) return;
  try {
    maplibre.addProtocol(
      "glyphs",
      localGlyphProtocol as Parameters<MapLibreModule["addProtocol"]>[1]
    );
  } catch (error) {
    if (!String(error).toLowerCase().includes("already")) {
      throw error;
    }
  }
  localGlyphProtocolRegistered = true;
}

const SHELTER_SOURCE_COLOR = [
  "case",
  ["==", ["get", "source_class"], "direct_unrouted_bus"],
  "#64748b",
  ["==", ["get", "is_covered"], 0],
  "#c4332b",
  ["==", ["get", "source_class"], "inferred_hdb_void_deck"],
  "#7d7a42",
  ["==", ["get", "source_class"], "bridge_underpass"],
  "#4f7690",
  ["==", ["get", "source_class"], "audited_shelter_correction"],
  "#6957a8",
  ["==", ["get", "source_class"], "osm_covered"],
  "#317d76",
  "#008f86",
] as unknown as maplibregl.ExpressionSpecification;

const SOURCE_IDS = [
  "transit-pois",
  "shortest-route",
  "shiokest-route",
  "exposure-gaps",
  "transit-node",
  "feedback-route",
  "feedback-points",
] as const;
const EMPTY_TRANSIT_POIS: TransitPoiCollection = { type: "FeatureCollection", features: [] };
const TRANSIT_POI_LAYER_IDS = [
  "mrt-station-halo",
  "mrt-station-dot",
  "mrt-station-label",
  "mrt-exit-dot",
  "mrt-exit-label",
  "bus-stop-dot",
  "bus-stop-label",
] as const;
const FEEDBACK_LAYER_IDS = [
  "feedback-route-casing",
  "feedback-route-line",
  "feedback-point-halo",
  "feedback-point-dot",
] as const;

function emptyCollection(): MapFeatureCollection {
  return { type: "FeatureCollection", features: [] };
}

function toProperCase(value: string): string {
  return value
    .toLowerCase()
    .replace(/\b([a-z])/g, (match) => match.toUpperCase())
    .replace(/\bMrt\b/g, "MRT")
    .replace(/\bLrt\b/g, "LRT")
    .replace(/\bHdb\b/g, "HDB")
    .replace(/\bAve\b/g, "Ave")
    .replace(/\bSt\b/g, "St");
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function asPopupText(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return null;
}

function formatPeakMinutes(value: unknown): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return `${value} min best`;
}

function poiPopupHtml(properties: Record<string, unknown>): string {
  const kind =
    properties.kind === "bus_stop"
      ? "Bus stop"
      : properties.kind === "mrt_station"
        ? "MRT/LRT station"
        : "MRT/LRT exit";
  const title = typeof properties.name === "string" ? toProperCase(properties.name) : kind;
  const rows: Array<[string, string]> = [];

  if (properties.kind === "bus_stop") {
    const code = asPopupText(properties.code);
    const road = asPopupText(properties.road);
    const services = asPopupText(properties.services) ?? asPopupText(properties.service_nos);
    const serviceCount = asPopupText(properties.service_count);
    const firstBus = asPopupText(properties.weekday_first_bus);
    const lastBus = asPopupText(properties.weekday_last_bus);
    const amPeak = formatPeakMinutes(properties.am_peak_best_min);
    const pmPeak = formatPeakMinutes(properties.pm_peak_best_min);
    const operators = asPopupText(properties.operators);
    if (code) rows.push(["Stop", code]);
    if (road) rows.push(["Road", toProperCase(road)]);
    if (services) rows.push(["Services", services]);
    if (!services && serviceCount) rows.push(["Services", serviceCount]);
    if (firstBus) rows.push(["First bus", firstBus]);
    if (lastBus) rows.push(["Last bus", lastBus]);
    if (amPeak) rows.push(["AM peak", amPeak]);
    if (pmPeak) rows.push(["PM peak", pmPeak]);
    if (operators) rows.push(["Operator", operators]);
  } else if (properties.kind === "mrt_station") {
    const exits = asPopupText(properties.exit_count);
    const system = asPopupText(properties.system);
    const lines = asPopupText(properties.lines) ?? asPopupText(properties.line);
    if (system) rows.push(["System", system]);
    if (exits) rows.push(["Exits", exits]);
    if (lines) rows.push(["Lines", lines]);
  } else {
    const station = asPopupText(properties.station);
    const exit = asPopupText(properties.exit);
    const system = asPopupText(properties.system);
    const lines = asPopupText(properties.lines) ?? asPopupText(properties.line);
    if (station) rows.push(["Station", toProperCase(station)]);
    if (exit) rows.push(["Exit", exit]);
    if (system) rows.push(["System", system]);
    if (lines) rows.push(["Lines", lines]);
  }

  const rowsHtml = rows
    .map(
      ([label, value]) =>
        `<dt style="font-weight:800;color:#43564f">${escapeHtml(label)}</dt><dd style="margin:0">${escapeHtml(
          value
        )}</dd>`
    )
    .join("");

  return `<strong style="display:block;color:#17211f;font-size:12px;line-height:1.25">${escapeHtml(
    title
  )}</strong><span style="display:block;color:#4f625b;font-size:11px;margin-top:2px">${escapeHtml(kind)}</span>${
    rows.length
      ? `<dl style="display:grid;grid-template-columns:auto 1fr;gap:2px 7px;margin:6px 0 0;color:#5f6f69;font-size:10px">${rowsHtml}</dl>`
      : ""
  }`;
}

function cleanPoiProperties(properties: Record<string, unknown>): Record<string, string | number> {
  const clean = Object.fromEntries(
    Object.entries(properties).filter(([, value]) => typeof value === "string" || typeof value === "number")
  ) as Record<string, string | number>;
  const label = poiLabelText(properties);
  return label ? { ...clean, label_text: label } : clean;
}

function poiLabelText(properties: Record<string, unknown>): string | null {
  const kind = asPopupText(properties.kind);
  if (kind === "mrt_station") {
    const label = asPopupText(properties.label);
    if (label) return toProperCase(label);
    const name = asPopupText(properties.name);
    return name ? toProperCase(name.replace(/\s+(MRT|LRT)\s+STATION$/i, "")) : null;
  }

  if (kind === "mrt_exit") {
    const exit = asPopupText(properties.exit);
    if (exit) return exit;
  }

  if (kind === "bus_stop") {
    const name = asPopupText(properties.name);
    if (name) return toProperCase(name);
    const code = asPopupText(properties.code);
    return code ? `Bus ${code}` : null;
  }

  return null;
}

function transitPoiCollection(pois: TransitPoiCollection): PointFeatureCollection {
  return {
    type: "FeatureCollection",
    features: pois.features
      .filter((feature) => feature.geometry?.type === "Point" && Array.isArray(feature.geometry.coordinates))
      .map((feature) => ({
        type: "Feature",
        geometry: {
          type: "Point",
          coordinates: feature.geometry.coordinates,
        },
        properties: cleanPoiProperties(feature.properties as unknown as Record<string, unknown>),
      })),
  };
}

function setSourceData(
  map: maplibregl.Map,
  sourceId: (typeof SOURCE_IDS)[number],
  data: MapFeatureCollection
) {
  const source = map.getSource(sourceId);
  if (source && "setData" in source && typeof source.setData === "function") {
    source.setData(data);
  }
}

function featureWithProps(
  feature: LineStringFeature,
  properties: Record<string, string | number>
): LineStringFeature {
  return {
    ...feature,
    properties: {
      ...feature.properties,
      ...properties,
    },
  };
}

function mergeCollections(collections: LineStringFeatureCollection[]): LineStringFeatureCollection {
  return {
    type: "FeatureCollection",
    features: collections.flatMap((collection) => collection.features),
  };
}

function endpointFor(collection: LineStringFeatureCollection): LngLat | null {
  const lastFeature = collection.features[collection.features.length - 1];
  const coordinates = lastFeature?.geometry.coordinates ?? [];
  return coordinates.length > 0 ? coordinates[coordinates.length - 1] : null;
}

function feedbackCollections(points: FeedbackPoint[]) {
  const coordinates: LngLat[] = points.map((point) => [point.lng, point.lat]);
  return {
    route: {
      type: "FeatureCollection",
      features:
        coordinates.length >= 2
          ? [
              {
                type: "Feature",
                geometry: {
                  type: "LineString",
                  coordinates,
                },
                properties: {
                  kind: "feedback_route",
                },
              },
            ]
          : [],
    } satisfies LineStringFeatureCollection,
    points: {
      type: "FeatureCollection",
      features: coordinates.map((coordinate, index) => ({
        type: "Feature",
        geometry: {
          type: "Point",
          coordinates: coordinate,
        },
        properties: {
          kind: "feedback_point",
          index: index + 1,
        },
      })),
    } satisfies PointFeatureCollection,
  };
}

function moveLayerToTop(map: maplibregl.Map, layerId: string) {
  if (map.getLayer(layerId)) {
    map.moveLayer(layerId);
  }
}

function raiseInteractivePointLayers(map: maplibregl.Map) {
  for (const layerId of TRANSIT_POI_LAYER_IDS) moveLayerToTop(map, layerId);
  for (const layerId of FEEDBACK_LAYER_IDS) moveLayerToTop(map, layerId);
}

function ensureRouteLayers(map: maplibregl.Map) {
  for (const id of SOURCE_IDS) {
    if (!map.getSource(id)) {
      map.addSource(id, {
        type: "geojson",
        data: emptyCollection(),
      });
    }
  }

  if (!map.getLayer("mrt-station-halo")) {
    map.addLayer({
      id: "mrt-station-halo",
      type: "circle",
      source: "transit-pois",
      minzoom: 10.8,
      filter: ["==", ["get", "kind"], "mrt_station"],
      paint: {
        "circle-color": "#ffffff",
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 10.8, 5.8, 17, 9],
        "circle-opacity": 0.92,
      },
    });
  }

  if (!map.getLayer("mrt-station-dot")) {
    map.addLayer({
      id: "mrt-station-dot",
      type: "circle",
      source: "transit-pois",
      minzoom: 10.8,
      filter: ["==", ["get", "kind"], "mrt_station"],
      paint: {
        "circle-color": "#245b8d",
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 10.8, 3.8, 17, 5.9],
        "circle-opacity": 0.92,
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 1,
      },
    });
  }

  if (!map.getLayer("mrt-station-label")) {
    map.addLayer({
      id: "mrt-station-label",
      type: "symbol",
      source: "transit-pois",
      minzoom: 11.2,
      filter: ["==", ["get", "kind"], "mrt_station"],
      layout: {
        "text-field": ["get", "label_text"],
        "text-font": ["Open Sans Regular"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 11.2, 10, 15, 12],
        "text-offset": [0, 1.05],
        "text-anchor": "top",
        "text-max-width": 9,
        "text-padding": 4,
        "text-optional": true,
      },
      paint: {
        "text-color": "#214861",
        "text-halo-color": "#f6faf8",
        "text-halo-width": 1.3,
        "text-opacity": ["interpolate", ["linear"], ["zoom"], 11.2, 0.72, 13, 0.94],
      },
    });
  }

  if (!map.getLayer("mrt-exit-dot")) {
    map.addLayer({
      id: "mrt-exit-dot",
      type: "circle",
      source: "transit-pois",
      minzoom: 13.8,
      filter: ["==", ["get", "kind"], "mrt_exit"],
      paint: {
        "circle-color": "#2f5f8f",
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 14.2, 2.5, 18, 4],
        "circle-opacity": 0.8,
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 0.75,
      },
    });
  }

  if (!map.getLayer("mrt-exit-label")) {
    map.addLayer({
      id: "mrt-exit-label",
      type: "symbol",
      source: "transit-pois",
      minzoom: 14.8,
      filter: ["==", ["get", "kind"], "mrt_exit"],
      layout: {
        "text-field": ["get", "label_text"],
        "text-font": ["Open Sans Regular"],
        "text-size": 10,
        "text-offset": [0.65, 0],
        "text-anchor": "left",
        "text-max-width": 5,
        "text-padding": 2,
        "text-optional": true,
      },
      paint: {
        "text-color": "#2f5f8f",
        "text-halo-color": "#f6faf8",
        "text-halo-width": 1.2,
        "text-opacity": 0.88,
      },
    });
  }

  if (!map.getLayer("bus-stop-dot")) {
    map.addLayer({
      id: "bus-stop-dot",
      type: "circle",
      source: "transit-pois",
      minzoom: 13.2,
      filter: ["==", ["get", "kind"], "bus_stop"],
      paint: {
        "circle-color": "#436b5f",
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 13.2, 1.7, 16, 2.9, 18, 4.2],
        "circle-opacity": ["interpolate", ["linear"], ["zoom"], 13.2, 0.5, 16, 0.82],
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 0.75,
      },
    });
  }

  if (!map.getLayer("bus-stop-label")) {
    map.addLayer({
      id: "bus-stop-label",
      type: "symbol",
      source: "transit-pois",
      minzoom: 15.5,
      filter: ["==", ["get", "kind"], "bus_stop"],
      layout: {
        "text-field": ["get", "label_text"],
        "text-font": ["Open Sans Regular"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 15.5, 9, 18, 10.5],
        "text-offset": [0.55, 0],
        "text-anchor": "left",
        "text-max-width": 8,
        "text-padding": 2,
        "text-optional": true,
      },
      paint: {
        "text-color": "#36594f",
        "text-halo-color": "#f6faf8",
        "text-halo-width": 1.1,
        "text-opacity": ["interpolate", ["linear"], ["zoom"], 15.5, 0.64, 18, 0.9],
      },
    });
  }

  if (!map.getLayer("shortest-route-casing")) {
    map.addLayer({
      id: "shortest-route-casing",
      type: "line",
      source: "shortest-route",
      paint: {
        "line-color": "#ffffff",
        "line-width": 6.2,
        "line-opacity": 0.84,
      },
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
    });
  }

  if (!map.getLayer("shortest-route-line")) {
    map.addLayer({
      id: "shortest-route-line",
      type: "line",
      source: "shortest-route",
      paint: {
        "line-color": [
          "case",
          ["has", "is_covered"],
          SHELTER_SOURCE_COLOR,
          "#26342f",
        ],
        "line-width": 3.5,
        "line-opacity": 0.9,
        "line-dasharray": [0.55, 1.25],
      },
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
    });
  }

  if (!map.getLayer("shiokest-route-casing")) {
    map.addLayer({
      id: "shiokest-route-casing",
      type: "line",
      source: "shiokest-route",
      paint: {
        "line-color": "#ffffff",
        "line-width": 8.2,
        "line-opacity": 0.86,
      },
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
    });
  }

  if (!map.getLayer("shiokest-route-line")) {
    map.addLayer({
      id: "shiokest-route-line",
      type: "line",
      source: "shiokest-route",
      paint: {
        "line-color": [
          "case",
          ["has", "is_covered"],
          SHELTER_SOURCE_COLOR,
          ["get", "color"],
        ],
        "line-width": 4.8,
        "line-opacity": 0.96,
      },
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
    });
  }

  if (!map.getLayer("exposure-gap-casing")) {
    map.addLayer({
      id: "exposure-gap-casing",
      type: "line",
      source: "exposure-gaps",
      paint: {
        "line-color": "#ffffff",
        "line-width": 5.4,
        "line-opacity": 0.82,
      },
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
    });
  }

  if (!map.getLayer("exposure-gap-line")) {
    map.addLayer({
      id: "exposure-gap-line",
      type: "line",
      source: "exposure-gaps",
      paint: {
        "line-color": "#c4332b",
        "line-width": 3.1,
        "line-opacity": 0.92,
        "line-dasharray": [0.35, 1.1],
      },
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
    });
  }

  if (!map.getLayer("transit-node-halo")) {
    map.addLayer({
      id: "transit-node-halo",
      type: "circle",
      source: "transit-node",
      paint: {
        "circle-color": "#ffffff",
        "circle-radius": 7,
        "circle-opacity": 0.95,
      },
    });
  }

  if (!map.getLayer("transit-node-dot")) {
    map.addLayer({
      id: "transit-node-dot",
      type: "circle",
      source: "transit-node",
      paint: {
        "circle-color": "#17211f",
        "circle-radius": 4,
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 1,
      },
    });
  }

  if (!map.getLayer("feedback-route-casing")) {
    map.addLayer({
      id: "feedback-route-casing",
      type: "line",
      source: "feedback-route",
      paint: {
        "line-color": "#ffffff",
        "line-width": 5.2,
        "line-opacity": 0.82,
      },
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
    });
  }

  if (!map.getLayer("feedback-route-line")) {
    map.addLayer({
      id: "feedback-route-line",
      type: "line",
      source: "feedback-route",
      paint: {
        "line-color": "#7b3f00",
        "line-width": 3,
        "line-opacity": 0.94,
        "line-dasharray": [1, 1.2],
      },
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
    });
  }

  if (!map.getLayer("feedback-point-halo")) {
    map.addLayer({
      id: "feedback-point-halo",
      type: "circle",
      source: "feedback-points",
      paint: {
        "circle-color": "#ffffff",
        "circle-radius": 6,
        "circle-opacity": 0.9,
      },
    });
  }

  if (!map.getLayer("feedback-point-dot")) {
    map.addLayer({
      id: "feedback-point-dot",
      type: "circle",
      source: "feedback-points",
      paint: {
        "circle-color": "#7b3f00",
        "circle-radius": 3.8,
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 1,
      },
    });
  }

  raiseInteractivePointLayers(map);
}

type PopupConstructor = typeof import("maplibre-gl").Popup;

function pointCoordinates(event: maplibregl.MapLayerMouseEvent): LngLat | null {
  const geometry = event.features?.[0]?.geometry;
  if (!geometry || geometry.type !== "Point") return null;
  const coordinates = geometry.coordinates;
  if (!Array.isArray(coordinates) || coordinates.length < 2) return null;
  return [Number(coordinates[0]), Number(coordinates[1])];
}

function bindPoiInteractions(map: maplibregl.Map, Popup: PopupConstructor) {
  for (const layerId of [
    "mrt-station-dot",
    "mrt-exit-dot",
    "bus-stop-dot",
  ]) {
    map.on("mouseenter", layerId, () => {
      map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", layerId, () => {
      map.getCanvas().style.cursor = "";
    });
    map.on("click", layerId, (event) => {
      const coordinates = pointCoordinates(event);
      if (!coordinates) return;
      const properties = (event.features?.[0]?.properties ?? {}) as Record<string, unknown>;
      new Popup({ closeButton: false, offset: 12 })
        .setLngLat(coordinates)
        .setHTML(poiPopupHtml(properties))
        .addTo(map);
    });
  }
}

function routeCollections(routes: RouteMapItem[], mode: RouteDisplayMode) {
  const shortestCollections: LineStringFeatureCollection[] = [];
  const shiokestCollections: LineStringFeatureCollection[] = [];
  const exposureCollections: LineStringFeatureCollection[] = [];
  const transitFeatures: PointFeature[] = [];
  const allBounds: [number, number][] = [];

  for (const route of routes) {
    const data = postalGeomToRouteGeoJson(route.geom);
    const shortest = mergeCollections([
      {
        type: "FeatureCollection",
        features: data.shortest.features.map((feature) =>
          featureWithProps(feature, {
            route_id: route.id,
            route_label: route.label,
            color: route.color,
          })
        ),
      },
    ]);
    const shiokest = mergeCollections([
      {
        type: "FeatureCollection",
        features: data.sheltered.features.map((feature) =>
          featureWithProps(feature, {
            route_id: route.id,
            route_label: route.label,
            color: route.color,
          })
        ),
      },
    ]);
    const exposure = mergeCollections([
      {
        type: "FeatureCollection",
        features: data.exposureGaps.features.map((feature) =>
          featureWithProps(feature, {
            route_id: route.id,
            route_label: route.label,
            color: "#c4332b",
          })
        ),
      },
    ]);

    if (mode === "shortest" || mode === "both") shortestCollections.push(shortest);
    if (mode === "shiokest" || mode === "both") {
      shiokestCollections.push(shiokest);
      exposureCollections.push(exposure);
    }

    const transitEndpoint = endpointFor(shiokest);
    if (transitEndpoint) {
      transitFeatures.push({
        type: "Feature",
        geometry: {
          type: "Point",
          coordinates: transitEndpoint,
        },
        properties: {
          kind: "transit_node",
          route_id: route.id,
          route_label: route.label,
        },
      });
    }

    if (data.bounds) {
      allBounds.push(data.bounds[0], data.bounds[1]);
    }
  }

  return {
    shortest: mergeCollections(shortestCollections),
    shiokest: mergeCollections(shiokestCollections),
    exposure: mergeCollections(exposureCollections),
    transit: {
      type: "FeatureCollection",
      features: transitFeatures,
    } satisfies PointFeatureCollection,
    bounds: boundsFor(allBounds),
  };
}

function routeModeLabel(mode: RouteDisplayMode): string {
  if (mode === "shortest") return "shortest route";
  if (mode === "both") return "shortest and Shiokest routes";
  return "Shiokest route";
}

function mapAriaLabel(routes: RouteMapItem[], mode: RouteDisplayMode): string {
  if (routes.length === 0) {
    return "Singapore transit map with MRT stations, LRT stations, and bus stops";
  }
  const labels = routes.map((route) => route.label).join(", ");
  return `Route evidence map for ${labels}, showing ${routeModeLabel(mode)}`;
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

function boundsFor(points: [number, number][]): [[number, number], [number, number]] | null {
  if (points.length === 0) return null;
  const lngs = points.map(([lng]) => lng);
  const lats = points.map(([, lat]) => lat);
  return [
    [Math.min(...lngs), Math.min(...lats)],
    [Math.max(...lngs), Math.max(...lats)],
  ];
}

export function RouteEvidenceMap({
  routes,
  mode,
  transitPois = EMPTY_TRANSIT_POIS,
  feedbackEnabled = false,
  feedbackPoints = [],
  onFeedbackPoint,
}: {
  routes: RouteMapItem[];
  mode: RouteDisplayMode;
  transitPois?: TransitPoiCollection;
  feedbackEnabled?: boolean;
  feedbackPoints?: FeedbackPoint[];
  onFeedbackPoint?: (point: FeedbackPoint) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const lastFitKeyRef = useRef<string>("");
  const [loaded, setLoaded] = useState(false);
  const routeData = useMemo(() => routeCollections(routes, mode), [routes, mode]);
  const routeFitKey = useMemo(
    () =>
      `${mode}:${routes
        .map((route) => `${route.id}:${route.geom.postal}:${route.geom.shortest}:${route.geom.sheltered}`)
        .join("|")}`,
    [routes, mode]
  );
  const transitPoiData = useMemo(() => transitPoiCollection(transitPois), [transitPois]);
  const feedbackData = useMemo(() => feedbackCollections(feedbackPoints), [feedbackPoints]);
  const accessibleLabel = useMemo(() => mapAriaLabel(routes, mode), [routes, mode]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let active = true;

    async function initMap() {
      const maplibre = await import("maplibre-gl");
      if (!active || !containerRef.current || mapRef.current) return;
      await ensureLocalGlyphProtocol(maplibre);
      if (!active || !containerRef.current || mapRef.current) return;

      mapRef.current = new maplibre.Map({
        container: containerRef.current,
        style: ONE_MAP_STYLE,
        center: [103.851959, 1.29027],
        zoom: 11.6,
        minZoom: 10,
        maxZoom: 19,
        maxBounds: SINGAPORE_BOUNDS,
        attributionControl: {
          compact: true,
        },
      });
      mapRef.current.addControl(new maplibre.NavigationControl({ showCompass: false }), "top-right");
      mapRef.current.on("load", () => {
        if (!mapRef.current) return;
        ensureRouteLayers(mapRef.current);
        bindPoiInteractions(mapRef.current, maplibre.Popup);
        setLoaded(true);
      });
    }

    void initMap();

    return () => {
      active = false;
      mapRef.current?.remove();
      mapRef.current = null;
      setLoaded(false);
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !loaded) return;
    ensureRouteLayers(map);

    setSourceData(map, "shortest-route", routeData.shortest);
    setSourceData(map, "shiokest-route", routeData.shiokest);
    setSourceData(map, "exposure-gaps", routeData.exposure);
    setSourceData(map, "transit-node", routeData.transit);
    setSourceData(map, "transit-pois", transitPoiData);
    setSourceData(map, "feedback-route", feedbackData.route);
    setSourceData(map, "feedback-points", feedbackData.points);
  }, [loaded, routeData, transitPoiData, feedbackData]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !loaded || !routeData.bounds) return;
    if (lastFitKeyRef.current === routeFitKey) return;
    lastFitKeyRef.current = routeFitKey;
    if (routeData.bounds) {
      const isCompact = map.getContainer().clientWidth < 700;
      map.fitBounds(routeData.bounds, {
        padding: isCompact
          ? { top: 300, right: 24, bottom: 90, left: 24 }
          : { top: 150, right: 80, bottom: 90, left: 390 },
        duration: prefersReducedMotion() ? 0 : 350,
        maxZoom: 16.6,
      });
    }
  }, [loaded, routeData.bounds, routeFitKey]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !loaded) return;

    map.getCanvas().style.cursor = feedbackEnabled ? "crosshair" : "";
    const handleClick = (event: maplibregl.MapMouseEvent) => {
      if (!feedbackEnabled || !onFeedbackPoint) return;
      onFeedbackPoint({
        lng: Number(event.lngLat.lng.toFixed(7)),
        lat: Number(event.lngLat.lat.toFixed(7)),
      });
    };

    map.on("click", handleClick);
    return () => {
      map.off("click", handleClick);
      if (map.getCanvas().style.cursor === "crosshair") {
        map.getCanvas().style.cursor = "";
      }
    };
  }, [feedbackEnabled, loaded, onFeedbackPoint]);

  return (
    <div className={styles.mapShell}>
      <div ref={containerRef} aria-label={accessibleLabel} role="img" className={styles.mapCanvas} />
    </div>
  );
}
