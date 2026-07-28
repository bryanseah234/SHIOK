"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import type maplibregl from "maplibre-gl";
import type { StyleSpecification } from "maplibre-gl";
import { postalGeomToRouteGeoJson } from "../lib/route-geojson";
import type { LineStringFeatureCollection, LineStringFeature } from "../lib/route-geojson";
import type { PostalGeom } from "../lib/types";
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

const ONE_MAP_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    onemap: {
      type: "raster",
      tiles: ["https://www.onemap.gov.sg/maps/tiles/Default_HD/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OneMap, Singapore Land Authority",
    },
  },
  layers: [
    {
      id: "onemap",
      type: "raster",
      source: "onemap",
      paint: {
        "raster-opacity": 0.72,
        "raster-saturation": -0.7,
        "raster-contrast": -0.25,
        "raster-brightness-min": 0.08,
        "raster-brightness-max": 0.92,
      },
    },
  ],
};

const SOURCE_IDS = ["shortest-route", "shiokest-route", "exposure-gaps"] as const;

function emptyCollection(): LineStringFeatureCollection {
  return { type: "FeatureCollection", features: [] };
}

function setSourceData(
  map: maplibregl.Map,
  sourceId: (typeof SOURCE_IDS)[number],
  data: LineStringFeatureCollection
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

function ensureRouteLayers(map: maplibregl.Map) {
  for (const id of SOURCE_IDS) {
    if (!map.getSource(id)) {
      map.addSource(id, {
        type: "geojson",
        data: emptyCollection(),
      });
    }
  }

  if (!map.getLayer("shortest-route-casing")) {
    map.addLayer({
      id: "shortest-route-casing",
      type: "line",
      source: "shortest-route",
      paint: {
        "line-color": "#ffffff",
        "line-width": 8,
        "line-opacity": 0.82,
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
        "line-width": 5.5,
        "line-opacity": 0.78,
        "line-dasharray": [1.1, 1.4],
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
        "line-width": 10,
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
        "line-color": ["get", "color"],
        "line-width": 7,
        "line-opacity": 0.94,
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
        "line-width": 11,
        "line-opacity": 0.9,
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
        "line-width": 8.5,
        "line-opacity": 0.96,
        "line-dasharray": [0.55, 1.05],
      },
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
    });
  }
}

function routeCollections(routes: RouteMapItem[], mode: RouteDisplayMode) {
  const shortestCollections: LineStringFeatureCollection[] = [];
  const shiokestCollections: LineStringFeatureCollection[] = [];
  const exposureCollections: LineStringFeatureCollection[] = [];
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

    if (data.bounds) {
      allBounds.push(data.bounds[0], data.bounds[1]);
    }
  }

  return {
    shortest: mergeCollections(shortestCollections),
    shiokest: mergeCollections(shiokestCollections),
    exposure: mergeCollections(exposureCollections),
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
  compareMode = false,
}: {
  routes: RouteMapItem[];
  mode: RouteDisplayMode;
  compareMode?: boolean;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [loaded, setLoaded] = useState(false);
  const routeData = useMemo(() => routeCollections(routes, mode), [routes, mode]);

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

    if (routeData.bounds) {
      const isCompact = map.getContainer().clientWidth < 700;
      map.fitBounds(routeData.bounds, {
        padding: isCompact
          ? { top: 230, right: 28, bottom: 360, left: 28 }
          : compareMode
            ? { top: 180, right: 70, bottom: 180, left: 70 }
            : { top: 150, right: 460, bottom: 80, left: 460 },
        duration: 350,
        maxZoom: 18,
      });
    }
  }, [compareMode, loaded, routeData]);

  return (
    <div className={styles.mapShell}>
      <div ref={containerRef} aria-label="Route evidence map" role="img" className={styles.mapCanvas} />
      {routes.length > 0 && (
        <div className={styles.legend} aria-label="Map legend">
          {(mode === "shiokest" || mode === "both") && (
            <span>
              <i className={styles.shiokestLine} />
              Shiokest
            </span>
          )}
          {(mode === "shortest" || mode === "both") && (
            <span>
              <i className={styles.shortestLine} />
              Shortest
            </span>
          )}
          {(mode === "shiokest" || mode === "both") && (
            <span>
              <i className={styles.gapLine} />
              Exposed
            </span>
          )}
        </div>
      )}
    </div>
  );
}
