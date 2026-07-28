"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import type maplibregl from "maplibre-gl";
import type { StyleSpecification } from "maplibre-gl";
import { postalGeomToRouteGeoJson } from "../lib/route-geojson";
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

const SINGAPORE_BOUNDS: [[number, number], [number, number]] = [
  [103.55, 1.13],
  [104.13, 1.49],
];

const ONE_MAP_TILE_BOUNDS = [103.596, 1.1443, 104.4309, 1.4835] as [number, number, number, number];

const ONE_MAP_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    onemap: {
      type: "raster",
      tiles: ["https://www.onemap.gov.sg/maps/tiles/GreyLite/{z}/{x}/{y}.png"],
      tileSize: 256,
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

const SOURCE_IDS = ["transit-pois", "shortest-route", "shiokest-route", "exposure-gaps", "transit-node"] as const;
const EMPTY_TRANSIT_POIS: TransitPoiCollection = { type: "FeatureCollection", features: [] };

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

function poiPopupHtml(properties: Record<string, unknown>): string {
  const kind = properties.kind === "bus_stop" ? "Bus stop" : "MRT/LRT exit";
  const title = typeof properties.name === "string" ? toProperCase(properties.name) : kind;
  const meta =
    properties.kind === "bus_stop"
      ? [properties.code, properties.road].filter((value): value is string => typeof value === "string")
      : [properties.station, properties.exit].filter((value): value is string => typeof value === "string");
  return `<strong style="display:block;color:#17211f;font-size:12px;line-height:1.25">${escapeHtml(
    title
  )}</strong><span style="display:block;color:#4f625b;font-size:11px;margin-top:2px">${escapeHtml(kind)}</span>${
    meta.length
      ? `<small style="display:block;color:#6b7a75;font-size:10px;margin-top:2px">${escapeHtml(
          meta.map(toProperCase).join(" / ")
        )}</small>`
      : ""
  }`;
}

function cleanPoiProperties(properties: Record<string, unknown>): Record<string, string | number> {
  return Object.fromEntries(
    Object.entries(properties).filter(([, value]) => typeof value === "string" || typeof value === "number")
  ) as Record<string, string | number>;
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
  const coordinates = collection.features[0]?.geometry.coordinates ?? [];
  return coordinates.length > 0 ? coordinates[coordinates.length - 1] : null;
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

  if (!map.getLayer("bus-stop-dot")) {
    map.addLayer({
      id: "bus-stop-dot",
      type: "circle",
      source: "transit-pois",
      minzoom: 15,
      filter: ["==", ["get", "kind"], "bus_stop"],
      paint: {
        "circle-color": "#5f766f",
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 15, 1.7, 18, 3.1],
        "circle-opacity": 0.56,
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 0.7,
      },
    });
  }

  if (!map.getLayer("mrt-exit-dot")) {
    map.addLayer({
      id: "mrt-exit-dot",
      type: "circle",
      source: "transit-pois",
      minzoom: 12,
      filter: ["==", ["get", "kind"], "mrt_exit"],
      paint: {
        "circle-color": "#2f5f8f",
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 12, 3.6, 17, 5.2],
        "circle-opacity": 0.86,
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 1.1,
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
        "line-width": 3.2,
        "line-opacity": 0.68,
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
        "line-color": "#34413d",
        "line-width": 1.45,
        "line-opacity": 0.72,
        "line-dasharray": [0.45, 1.75],
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
        "line-width": 4.2,
        "line-opacity": 0.72,
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
        "line-color": ["get", "color"],
        "line-width": 2.45,
        "line-opacity": 0.9,
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
        "line-width": 4,
        "line-opacity": 0.78,
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
        "line-width": 2,
        "line-opacity": 0.9,
        "line-dasharray": [0.35, 1.5],
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
  for (const layerId of ["mrt-exit-dot", "bus-stop-dot"]) {
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
}: {
  routes: RouteMapItem[];
  mode: RouteDisplayMode;
  transitPois?: TransitPoiCollection;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [loaded, setLoaded] = useState(false);
  const routeData = useMemo(() => routeCollections(routes, mode), [routes, mode]);
  const transitPoiData = useMemo(() => transitPoiCollection(transitPois), [transitPois]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let active = true;

    async function initMap() {
      const maplibre = await import("maplibre-gl");
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

    if (routeData.bounds) {
      const isCompact = map.getContainer().clientWidth < 700;
      map.fitBounds(routeData.bounds, {
        padding: isCompact
          ? { top: 300, right: 24, bottom: 90, left: 24 }
          : { top: 150, right: 80, bottom: 90, left: 390 },
        duration: 350,
        maxZoom: 16.2,
      });
    }
  }, [loaded, routeData, transitPoiData]);

  return (
    <div className={styles.mapShell}>
      <div ref={containerRef} aria-label="Route evidence map" role="img" className={styles.mapCanvas} />
    </div>
  );
}
