"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import type maplibregl from "maplibre-gl";
import type { StyleSpecification } from "maplibre-gl";
import { postalGeomToRouteGeoJson } from "../lib/route-geojson";
import localGlyphProtocol from "../lib/local-glyph-protocol";
import { cleanTransitPoiProperties, transitPoiPopupHtml } from "../lib/transit-popup";
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
/** Invisible larger circles under the visual dots for accessible tap targets. */
const TRANSIT_POI_HIT_LAYER_IDS = [
  "mrt-station-hit",
  "mrt-exit-hit",
  "bus-stop-hit",
] as const;
/** Highlight ring layers rendered around the currently chosen POI. */
const TRANSIT_POI_ACTIVE_RING_LAYER_IDS = [
  "mrt-station-active-ring",
  "mrt-exit-active-ring",
  "bus-stop-active-ring",
] as const;
/** All layers we query on map click when detecting a stop selection. */
const TRANSIT_POI_CLICK_LAYER_IDS = [
  ...TRANSIT_POI_HIT_LAYER_IDS,
  "mrt-station-dot",
  "mrt-exit-dot",
  "bus-stop-dot",
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
        properties: cleanTransitPoiProperties(feature.properties as unknown as Record<string, unknown>),
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
  for (const layerId of TRANSIT_POI_HIT_LAYER_IDS) moveLayerToTop(map, layerId);
  for (const layerId of TRANSIT_POI_LAYER_IDS) moveLayerToTop(map, layerId);
  for (const layerId of TRANSIT_POI_ACTIVE_RING_LAYER_IDS) moveLayerToTop(map, layerId);
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
      minzoom: 9.8,
      filter: ["==", ["get", "kind"], "mrt_station"],
      paint: {
        "circle-color": "#ffffff",
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 9.8, 5.8, 17, 9],
        "circle-opacity": 0.92,
      },
    });
  }

  // Invisible larger hit target for accessible tap on MRT stations (~40% larger).
  if (!map.getLayer("mrt-station-hit")) {
    map.addLayer({
      id: "mrt-station-hit",
      type: "circle",
      source: "transit-pois",
      minzoom: 9.8,
      filter: ["==", ["get", "kind"], "mrt_station"],
      paint: {
        "circle-color": "#000000",
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 9.8, 12, 17, 14],
        "circle-opacity": 0,
      },
    });
  }

  if (!map.getLayer("mrt-station-dot")) {
    map.addLayer({
      id: "mrt-station-dot",
      type: "circle",
      source: "transit-pois",
      minzoom: 9.8,
      filter: ["==", ["get", "kind"], "mrt_station"],
      paint: {
        "circle-color": "#245b8d",
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 9.8, 3.8, 17, 5.9],
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
      minzoom: 10.4,
      filter: ["==", ["get", "kind"], "mrt_station"],
      layout: {
        "text-field": ["get", "label_text"],
        "text-font": ["Open Sans Regular"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 10.4, 10, 15, 12],
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
        "text-opacity": ["interpolate", ["linear"], ["zoom"], 10.8, 0.72, 13, 0.94],
      },
    });
  }

  if (!map.getLayer("mrt-exit-dot")) {
    map.addLayer({
      id: "mrt-exit-dot",
      type: "circle",
      source: "transit-pois",
      minzoom: 13.2,
      filter: ["==", ["get", "kind"], "mrt_exit"],
      paint: {
        "circle-color": "#2f5f8f",
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 13.2, 2.4, 18, 4],
        "circle-opacity": 0.8,
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 0.75,
      },
    });
  }

  if (!map.getLayer("mrt-exit-hit")) {
    map.addLayer({
      id: "mrt-exit-hit",
      type: "circle",
      source: "transit-pois",
      minzoom: 13.2,
      filter: ["==", ["get", "kind"], "mrt_exit"],
      paint: {
        "circle-color": "#000000",
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 13.2, 8, 18, 12],
        "circle-opacity": 0,
      },
    });
  }

  if (!map.getLayer("mrt-exit-label")) {
    map.addLayer({
      id: "mrt-exit-label",
      type: "symbol",
      source: "transit-pois",
      minzoom: 14.2,
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
      minzoom: 12.0,
      filter: ["==", ["get", "kind"], "bus_stop"],
      paint: {
        "circle-color": "#436b5f",
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 12.0, 1.9, 16, 3.3, 18, 4.6],
        "circle-opacity": ["interpolate", ["linear"], ["zoom"], 12.0, 0.6, 16, 0.9],
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 0.75,
      },
    });
  }

  if (!map.getLayer("bus-stop-hit")) {
    map.addLayer({
      id: "bus-stop-hit",
      type: "circle",
      source: "transit-pois",
      minzoom: 12.0,
      filter: ["==", ["get", "kind"], "bus_stop"],
      paint: {
        "circle-color": "#000000",
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 12.0, 7, 16, 10, 18, 13],
        "circle-opacity": 0,
      },
    });
  }

  if (!map.getLayer("bus-stop-label")) {
    map.addLayer({
      id: "bus-stop-label",
      type: "symbol",
      source: "transit-pois",
      minzoom: 14.6,
      filter: ["==", ["get", "kind"], "bus_stop"],
      layout: {
        "text-field": ["get", "label_text"],
        "text-font": ["Open Sans Regular"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 14.6, 9, 18, 10.5],
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
        "text-opacity": ["interpolate", ["linear"], ["zoom"], 15.0, 0.64, 18, 0.9],
      },
    });
  }

  // Active-stop highlight rings. Filter starts as "match nothing"; the effect
  // that owns the chosenStopId prop updates the filter at runtime.
  if (!map.getLayer("mrt-station-active-ring")) {
    map.addLayer({
      id: "mrt-station-active-ring",
      type: "circle",
      source: "transit-pois",
      minzoom: 9.8,
      filter: ["all", ["==", ["get", "kind"], "mrt_station"], ["==", ["get", "id"], "__none__"]],
      paint: {
        "circle-color": "rgba(0,0,0,0)",
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 9.8, 8.4, 17, 12],
        "circle-stroke-color": "#f5a623",
        "circle-stroke-width": 2.4,
        "circle-stroke-opacity": 0.94,
      },
    });
  }

  if (!map.getLayer("mrt-exit-active-ring")) {
    map.addLayer({
      id: "mrt-exit-active-ring",
      type: "circle",
      source: "transit-pois",
      minzoom: 13.2,
      filter: ["all", ["==", ["get", "kind"], "mrt_exit"], ["==", ["get", "id"], "__none__"]],
      paint: {
        "circle-color": "rgba(0,0,0,0)",
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 13.2, 4.4, 18, 6.5],
        "circle-stroke-color": "#f5a623",
        "circle-stroke-width": 2.2,
        "circle-stroke-opacity": 0.94,
      },
    });
  }

  if (!map.getLayer("bus-stop-active-ring")) {
    map.addLayer({
      id: "bus-stop-active-ring",
      type: "circle",
      source: "transit-pois",
      minzoom: 12.0,
      filter: ["all", ["==", ["get", "kind"], "bus_stop"], ["==", ["get", "id"], "__none__"]],
      paint: {
        "circle-color": "rgba(0,0,0,0)",
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 12.0, 4, 16, 6, 18, 8.4],
        "circle-stroke-color": "#f5a623",
        "circle-stroke-width": 2.2,
        "circle-stroke-opacity": 0.94,
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
        "line-width": 8.4,
        "line-opacity": 0.88,
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
        "line-width": 4.8,
        "line-opacity": 0.95,
        "line-dasharray": [0.65, 1.05],
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
        "line-width": 11,
        "line-opacity": 0.88,
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
        "line-width": 6.8,
        "line-opacity": 0.98,
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
        "line-width": 6.4,
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
        "line-width": 3.8,
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
    "mrt-station-hit",
    "mrt-exit-dot",
    "mrt-exit-hit",
    "bus-stop-dot",
    "bus-stop-hit",
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
        .setHTML(transitPoiPopupHtml(properties))
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

function transitPoiSummary(pois: PointFeatureCollection): string {
  const counts = pois.features.reduce(
    (total, feature) => {
      const kind = feature.properties.kind;
      if (kind === "mrt_station") total.mrtStations += 1;
      if (kind === "mrt_exit") total.mrtExits += 1;
      if (kind === "bus_stop") total.busStops += 1;
      return total;
    },
    { mrtStations: 0, mrtExits: 0, busStops: 0 }
  );

  return `${counts.mrtStations} MRT or LRT stations, ${counts.mrtExits} exits, and ${counts.busStops} bus stops`;
}

function mapTextSummary(
  routes: RouteMapItem[],
  mode: RouteDisplayMode,
  routeData: ReturnType<typeof routeCollections>,
  pois: PointFeatureCollection
): string {
  const poiText = transitPoiSummary(pois);
  if (routes.length === 0) {
    return `Singapore map with ${poiText}. Search for a postal code to show route evidence.`;
  }

  const routeLabels = routes.map((route) => route.label).join(", ");
  const visibleRoutes =
    mode === "both"
      ? `${routeData.shiokest.features.length} Shiokest segments and ${routeData.shortest.features.length} shortest segments`
      : mode === "shortest"
        ? `${routeData.shortest.features.length} shortest segments`
        : `${routeData.shiokest.features.length} Shiokest segments`;
  const exposed =
    routeData.exposure.features.length === 1
      ? "1 exposed gap"
      : `${routeData.exposure.features.length} exposed gaps`;

  return `Route evidence for ${routeLabels}. Showing ${visibleRoutes}, ${exposed}, and ${poiText}.`;
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
  onSelectTransitStop,
  chosenStopId = null,
}: {
  routes: RouteMapItem[];
  mode: RouteDisplayMode;
  transitPois?: TransitPoiCollection;
  feedbackEnabled?: boolean;
  feedbackPoints?: FeedbackPoint[];
  onFeedbackPoint?: (point: FeedbackPoint) => void;
  /** Optional handler wired to click / tap on a transit POI (bus stop or MRT exit). */
  onSelectTransitStop?: (stopId: string) => void;
  /** POI id of the currently highlighted stop; enables the active ring layer. */
  chosenStopId?: string | null;
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
  const accessibleSummary = useMemo(
    () => mapTextSummary(routes, mode, routeData, transitPoiData),
    [routes, mode, routeData, transitPoiData]
  );

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

  const onSelectTransitStopRef = useRef(onSelectTransitStop);
  useEffect(() => {
    onSelectTransitStopRef.current = onSelectTransitStop;
  }, [onSelectTransitStop]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !loaded) return;

    map.getCanvas().style.cursor = feedbackEnabled ? "crosshair" : "";
    const handleClick = (event: maplibregl.MapMouseEvent) => {
      if (feedbackEnabled) {
        if (onFeedbackPoint) {
          onFeedbackPoint({
            lng: Number(event.lngLat.lng.toFixed(7)),
            lat: Number(event.lngLat.lat.toFixed(7)),
          });
        }
        return;
      }
      const selectHandler = onSelectTransitStopRef.current;
      if (!selectHandler) return;
      const clickLayers = TRANSIT_POI_CLICK_LAYER_IDS.filter((layerId) =>
        map.getLayer(layerId)
      );
      if (clickLayers.length === 0) return;
      const features = map.queryRenderedFeatures(event.point, { layers: clickLayers });
      const hit = features.find((feature) => {
        const rawId = feature.properties?.id;
        return typeof rawId === "string" && rawId.length > 0;
      });
      const stopId = hit?.properties?.id;
      if (typeof stopId === "string" && stopId.length > 0) {
        selectHandler(stopId);
      }
    };

    map.on("click", handleClick);
    return () => {
      map.off("click", handleClick);
      if (map.getCanvas().style.cursor === "crosshair") {
        map.getCanvas().style.cursor = "";
      }
    };
  }, [feedbackEnabled, loaded, onFeedbackPoint]);

  // Keep the highlight-ring layers filtered to the currently chosen stop id.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !loaded) return;
    const idToken = typeof chosenStopId === "string" && chosenStopId.length > 0
      ? chosenStopId
      : "__none__";
    for (const layerId of TRANSIT_POI_ACTIVE_RING_LAYER_IDS) {
      if (!map.getLayer(layerId)) continue;
      const kind = layerId.startsWith("mrt-station")
        ? "mrt_station"
        : layerId.startsWith("mrt-exit")
          ? "mrt_exit"
          : "bus_stop";
      map.setFilter(layerId, [
        "all",
        ["==", ["get", "kind"], kind],
        ["==", ["get", "id"], idToken],
      ]);
    }
  }, [chosenStopId, loaded]);

  return (
    <div className={styles.mapShell}>
      <div
        ref={containerRef}
        aria-describedby="route-map-summary"
        aria-label={accessibleLabel}
        role="img"
        tabIndex={0}
        className={styles.mapCanvas}
      />
      <p id="route-map-summary" className={styles.screenReaderOnly}>
        {accessibleSummary}
      </p>
    </div>
  );
}
