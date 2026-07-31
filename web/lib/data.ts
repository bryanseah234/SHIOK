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
type ScorePrefixIndex = Record<string, string[]>;
const DATA_FETCH_OPTIONS: RequestInit = { cache: "no-store" };

function dataUrl(path: string): string {
  const url = `${DATA_BASE}${path}`;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}v=${encodeURIComponent(DATA_FETCH_VERSION)}`;
}

async function decodeJsonResponse<T>(res: Response, path: string): Promise<T> {
  const contentEncoding = res.headers?.get("content-encoding") ?? "";
  if (path.endsWith(".gz") && !contentEncoding.toLowerCase().includes("gzip")) {
    if (!res.body || typeof DecompressionStream === "undefined") {
      throw new Error(`gzip data fetch is unsupported for ${path}`);
    }
    const stream = res.body.pipeThrough(new DecompressionStream("gzip"));
    return new Response(stream).json() as Promise<T>;
  }
  return res.json() as Promise<T>;
}

async function fetchJson<T>(path: string): Promise<T> {
  const gzPath = `${path}.gz`;
  const gzRes = await fetch(dataUrl(gzPath), DATA_FETCH_OPTIONS);
  if (gzRes.ok) return decodeJsonResponse<T>(gzRes, gzPath);

  const res = await fetch(dataUrl(path), DATA_FETCH_OPTIONS);
  if (!res.ok) throw new Error(`${path} fetch failed: ${res.status}`);
  return decodeJsonResponse<T>(res, path);
}

// ---------------------------------------------------------------------------
// Manifest
// ---------------------------------------------------------------------------
export async function fetchManifest(): Promise<Manifest> {
  return fetchJson<Manifest>("manifest.json");
}

export async function fetchTransitPois(): Promise<TransitPoiCollection> {
  if (_transitPois) return _transitPois;
  try {
    const payload = await fetchJson<TransitPoiCollection>("transit/pois.json");
    _transitPois = {
      type: "FeatureCollection",
      features: Array.isArray(payload.features) ? payload.features : [],
      provenance: payload.provenance,
    };
    return _transitPois;
  } catch {
    _transitPois = { type: "FeatureCollection", features: [] };
    return _transitPois;
  }
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
let _scorePrefixIndex: ScorePrefixIndex | null | undefined;
let _geomIndex: GeomIndex | null = null;
let _geomPostalIndex: GeomPostalIndex | null = null;
let _transitPois: TransitPoiCollection | null = null;

async function getAreaIndex(): Promise<Record<string, string[]>> {
  if (_areaIndex) return _areaIndex;
  _areaIndex = await fetchJson<Record<string, string[]>>("scores/index.json");
  return _areaIndex!;
}

async function getScorePrefixIndex(): Promise<ScorePrefixIndex | null> {
  if (_scorePrefixIndex !== undefined) return _scorePrefixIndex;
  try {
    _scorePrefixIndex = await fetchJson<ScorePrefixIndex>("scores/prefix-index.json");
  } catch {
    _scorePrefixIndex = null;
  }
  return _scorePrefixIndex;
}

async function fetchAreaRecords(areaSlug: string): Promise<ScoreRecord[]> {
  return fetchJson<ScoreRecord[]>(`scores/${areaSlug}.json`);
}

export async function fetchScoreForPostal(
  postal: string
): Promise<ScoreRecord | null> {
  const tried = new Set<string>();
  const prefixIndex = await getScorePrefixIndex();
  for (const shard of prefixIndex?.[postal.slice(0, 3)] ?? []) {
    tried.add(shard);
    const records = await fetchAreaRecords(shard);
    const match = records.find((r) => r.postal === postal);
    if (match) return match;
  }

  const index = await getAreaIndex();
  for (const [slug, postals] of Object.entries(index)) {
    if (tried.has(slug)) continue;
    if (postals.includes(postal)) {
      const records = await fetchAreaRecords(slug);
      return records.find((r) => r.postal === postal) ?? null;
    }
  }
  return null;
}

async function getGeomIndex(): Promise<GeomIndex | null> {
  if (_geomIndex) return _geomIndex;
  try {
    _geomIndex = await fetchJson<GeomIndex>("geom/index.json");
    return _geomIndex;
  } catch {
    return null;
  }
}

async function getGeomPostalIndex(): Promise<GeomPostalIndex | null> {
  if (_geomPostalIndex) return _geomPostalIndex;
  try {
    _geomPostalIndex = await fetchJson<GeomPostalIndex>("geom/postal-index.json");
    return _geomPostalIndex;
  } catch {
    return null;
  }
}

async function fetchGeomShard(shardId: string): Promise<PostalGeom[] | null> {
  try {
    return await fetchJson<PostalGeom[]>(`geom/h3/${shardId}.json`);
  } catch {
    return null;
  }
}

async function fetchGeomByLatLng(
  postal: string,
  lat: number,
  lng: number
): Promise<PostalGeom | null> {
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
  if (typeof lat === "number" && typeof lng === "number") {
    const latLngMatch = await fetchGeomByLatLng(postal, lat, lng);
    if (latLngMatch) return latLngMatch;
  }

  const postalIndex = await getGeomPostalIndex();
  const indexedShard = postalIndex?.[postal];
  if (indexedShard) {
    const records = await fetchGeomShard(indexedShard);
    const match = records?.find((r) => r.postal === postal);
    if (match) return match;
  }

  return null;
}
