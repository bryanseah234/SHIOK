"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import type maplibregl from "maplibre-gl";
import type { StyleSpecification } from "maplibre-gl";
import { postalGeomToRouteGeoJson } from "../lib/route-geojson";
import type { LineStringFeatureCollection } from "../lib/route-geojson";
import type { PostalGeom } from "../lib/types";

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
    },
  ],
};

const SOURCE_IDS = ["shortest-route", "sheltered-route", "exposure-gaps"] as const;

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

function ensureRouteLayers(map: maplibregl.Map) {
  if (!map.getSource("shortest-route")) {
    map.addSource("shortest-route", {
      type: "geojson",
      data: emptyCollection(),
    });
  }
  if (!map.getSource("sheltered-route")) {
    map.addSource("sheltered-route", {
      type: "geojson",
      data: emptyCollection(),
    });
  }
  if (!map.getSource("exposure-gaps")) {
    map.addSource("exposure-gaps", {
      type: "geojson",
      data: emptyCollection(),
    });
  }

  if (!map.getLayer("shortest-route-line")) {
    map.addLayer({
      id: "shortest-route-line",
      type: "line",
      source: "shortest-route",
      paint: {
        "line-color": "#475569",
        "line-width": 5,
        "line-opacity": 0.72,
        "line-dasharray": [1.1, 1.4],
      },
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
    });
  }
  if (!map.getLayer("sheltered-route-line")) {
    map.addLayer({
      id: "sheltered-route-line",
      type: "line",
      source: "sheltered-route",
      paint: {
        "line-color": "#0284c7",
        "line-width": 5,
        "line-opacity": 0.95,
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
        "line-color": "#dc2626",
        "line-width": 7,
        "line-opacity": 0.92,
        "line-dasharray": [0.7, 1.1],
      },
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
    });
  }
}

export function RouteEvidenceMap({ geom }: { geom: PostalGeom | null }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [loaded, setLoaded] = useState(false);
  const routeData = useMemo(() => (geom ? postalGeomToRouteGeoJson(geom) : null), [geom]);

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
        zoom: 12,
        minZoom: 10,
        maxZoom: 19,
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

    if (!routeData) {
      setSourceData(map, "shortest-route", emptyCollection());
      setSourceData(map, "sheltered-route", emptyCollection());
      setSourceData(map, "exposure-gaps", emptyCollection());
      return;
    }

    setSourceData(map, "shortest-route", routeData.shortest);
    setSourceData(map, "sheltered-route", routeData.sheltered);
    setSourceData(map, "exposure-gaps", routeData.exposureGaps);

    if (routeData.bounds) {
      map.fitBounds(routeData.bounds, {
        padding: { top: 48, right: 48, bottom: 76, left: 48 },
        duration: 350,
        maxZoom: 18,
      });
    } else if (routeData.center) {
      map.easeTo({ center: routeData.center, zoom: 16, duration: 350 });
    }
  }, [loaded, routeData]);

  return (
    <div style={mapShellStyle}>
      <div ref={containerRef} aria-label="Route evidence map" role="img" style={mapCanvasStyle} />
      {!geom && (
        <div style={emptyOverlayStyle}>
          <strong>Search a postal or address</strong>
          <span>Routes draw here when the postal has a scored transit path.</span>
        </div>
      )}
      <div style={legendStyle}>
        <span style={legendItemStyle}>
          <i style={{ ...legendLineStyle, background: "#0284c7" }} />
          Sheltered route
        </span>
        <span style={legendItemStyle}>
          <i style={{ ...legendLineStyle, background: "#475569", opacity: 0.72 }} />
          Shortest route
        </span>
        <span style={legendItemStyle}>
          <i style={{ ...legendLineStyle, background: "#dc2626" }} />
          Exposed gaps
        </span>
      </div>
    </div>
  );
}

const mapShellStyle: React.CSSProperties = {
  border: "1px solid #d8dee7",
  borderRadius: "8px",
  overflow: "hidden",
  height: "min(72vh, 760px)",
  minHeight: "520px",
  position: "relative",
  background: "#eef2f7",
};

const mapCanvasStyle: React.CSSProperties = {
  width: "100%",
  height: "100%",
};

const legendStyle: React.CSSProperties = {
  position: "absolute",
  left: "10px",
  top: "10px",
  maxWidth: "calc(100% - 84px)",
  display: "flex",
  flexWrap: "wrap",
  gap: "8px",
  padding: "8px 10px",
  border: "1px solid rgba(148, 163, 184, 0.45)",
  borderRadius: "8px",
  background: "rgba(255, 255, 255, 0.94)",
  color: "#334155",
  fontSize: "12px",
  boxShadow: "0 8px 20px rgba(15, 23, 42, 0.12)",
};

const legendItemStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "6px",
  whiteSpace: "nowrap",
};

const legendLineStyle: React.CSSProperties = {
  display: "inline-block",
  width: "24px",
  height: "4px",
  borderRadius: "999px",
};

const emptyOverlayStyle: React.CSSProperties = {
  position: "absolute",
  left: "50%",
  top: "50%",
  transform: "translate(-50%, -50%)",
  width: "min(340px, calc(100% - 40px))",
  border: "1px solid rgba(148, 163, 184, 0.45)",
  borderRadius: "8px",
  display: "flex",
  flexDirection: "column",
  justifyContent: "center",
  alignItems: "center",
  gap: "6px",
  color: "#334155",
  textAlign: "center",
  padding: "16px",
  background: "rgba(255, 255, 255, 0.95)",
  boxShadow: "0 16px 40px rgba(15, 23, 42, 0.16)",
};
