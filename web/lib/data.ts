/**
 * DATA ACCESS MODULE
 * Defaults to the latest validated static score bundle.
 */
import dataBundle from "../data-bundle.json";

export const DEFAULT_DATA_BASE = `/data/${dataBundle.bundle}/`;

export function normalizeDataBase(value?: string): string {
  const raw = value?.trim();
  if (!raw) {
    return DEFAULT_DATA_BASE;
  }
  const withLeadingSlash =
    raw.startsWith("http://") || raw.startsWith("https://") || raw.startsWith("/")
      ? raw
      : `/${raw}`;
  return withLeadingSlash.endsWith("/") ? withLeadingSlash : `${withLeadingSlash}/`;
}

export const DATA_BASE = normalizeDataBase(process.env.NEXT_PUBLIC_DATA_BASE);
const DATA_FETCH_VERSION = `${dataBundle.bundle}-${Date.now().toString(36)}`;

import type { ScoreRecord, PostalGeom, Manifest, TransitPoiCollection } from "./types";
import { latLngToCell } from "h3-js";

type GeomIndex = Record<string, string[]>;
type GeomPostalIndex = Record<string, string>;
const DATA_FETCH_OPTIONS: RequestInit = { cache: "no-store" };

function dataUrl(path: string): string {
  const url = `${DATA_BASE}${path}`;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}v=${encodeURIComponent(DATA_FETCH_VERSION)}`;
}

// ---------------------------------------------------------------------------
// Manifest
// ---------------------------------------------------------------------------
export async function fetchManifest(): Promise<Manifest> {
  const res = await fetch(dataUrl("manifest.json"), DATA_FETCH_OPTIONS);
  if (!res.ok) throw new Error(`manifest fetch failed: ${res.status}`);
  return res.json() as Promise<Manifest>;
}

export async function fetchTransitPois(): Promise<TransitPoiCollection> {
  if (_transitPois) return _transitPois;
  const res = await fetch(dataUrl("transit/pois.json"), DATA_FETCH_OPTIONS);
  if (!res.ok) {
    _transitPois = { type: "FeatureCollection", features: [] };
    return _transitPois;
  }
  const payload = (await res.json()) as TransitPoiCollection;
  _transitPois = {
    type: "FeatureCollection",
    features: Array.isArray(payload.features) ? payload.features : [],
    provenance: payload.provenance,
  };
  return _transitPois;
}

// ---------------------------------------------------------------------------
// Score records
// The pipeline shards scores by planning-area into files named by planning
// area slug (e.g. "ANG_MO_KIO.json").  For the mock we use a single area
// file that holds all five test postals.  fetchScoreForPostal searches every
// area file via the area-to-postals index; in mock mode the index is trivial.
// ---------------------------------------------------------------------------

/** Area-index maps area-slug → [postal, …] so we can look up which file to fetch. */
let _areaIndex: Record<string, string[]> | null = null;
let _geomIndex: GeomIndex | null = null;
let _geomPostalIndex: GeomPostalIndex | null = null;
let _transitPois: TransitPoiCollection | null = null;

async function getAreaIndex(): Promise<Record<string, string[]>> {
  if (_areaIndex) return _areaIndex;
  const res = await fetch(dataUrl("scores/index.json"), DATA_FETCH_OPTIONS);
  if (!res.ok) throw new Error(`score index fetch failed: ${res.status}`);
  _areaIndex = await res.json();
  return _areaIndex!;
}

async function fetchAreaRecords(areaSlug: string): Promise<ScoreRecord[]> {
  const res = await fetch(dataUrl(`scores/${areaSlug}.json`), DATA_FETCH_OPTIONS);
  if (!res.ok) throw new Error(`area fetch failed for ${areaSlug}: ${res.status}`);
  return res.json() as Promise<ScoreRecord[]>;
}

export async function fetchScoreForPostal(
  postal: string
): Promise<ScoreRecord | null> {
  const index = await getAreaIndex();
  for (const [slug, postals] of Object.entries(index)) {
    if (postals.includes(postal)) {
      const records = await fetchAreaRecords(slug);
      return records.find((r) => r.postal === postal) ?? null;
    }
  }
  return null;
}

async function getGeomIndex(): Promise<GeomIndex | null> {
  if (_geomIndex) return _geomIndex;
  const res = await fetch(dataUrl("geom/index.json"), DATA_FETCH_OPTIONS);
  if (!res.ok) return null;
  _geomIndex = (await res.json()) as GeomIndex;
  return _geomIndex;
}

async function getGeomPostalIndex(): Promise<GeomPostalIndex | null> {
  if (_geomPostalIndex) return _geomPostalIndex;
  const res = await fetch(dataUrl("geom/postal-index.json"), DATA_FETCH_OPTIONS);
  if (!res.ok) return null;
  _geomPostalIndex = (await res.json()) as GeomPostalIndex;
  return _geomPostalIndex;
}

async function fetchGeomShard(shardId: string): Promise<PostalGeom[] | null> {
  const res = await fetch(dataUrl(`geom/h3/${shardId}.json`), DATA_FETCH_OPTIONS);
  if (!res.ok) return null;
  return res.json() as Promise<PostalGeom[]>;
}

// ---------------------------------------------------------------------------
// Geometry
// The client resolves which H3 res-8 shard to fetch using the postal's lat/lng
// (supplied from the OneMap search result).  geom/h3/{cell}.json holds an
// array of PostalGeom for all postals in that cell.
// ---------------------------------------------------------------------------
export async function fetchGeomForPostal(
  postal: string,
  lat?: number,
  lng?: number
): Promise<PostalGeom | null> {
  const postalIndex = await getGeomPostalIndex();
  const indexedShard = postalIndex?.[postal];
  if (indexedShard) {
    const records = await fetchGeomShard(indexedShard);
    const match = records?.find((r) => r.postal === postal);
    if (match) return match;
  }

  if (typeof lat === "number" && typeof lng === "number") {
    const cell = latLngToCell(lat, lng, 8);
    const parentRecords = await fetchGeomShard(cell);
    const parentMatch = parentRecords?.find((r) => r.postal === postal);
    if (parentMatch) return parentMatch;

    const index = await getGeomIndex();
    for (const child of index?.[cell] ?? []) {
      const childRecords = await fetchGeomShard(child);
      const childMatch = childRecords?.find((r) => r.postal === postal);
      if (childMatch) return childMatch;
    }

    if (!parentRecords && !(index?.[cell]?.length)) {
      console.warn(`geom shard not found for cell ${cell} (postal ${postal})`);
    }
    return null;
  }

  return null;
}
