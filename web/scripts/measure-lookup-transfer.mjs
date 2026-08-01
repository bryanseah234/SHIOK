import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { gunzipSync, gzipSync } from "node:zlib";
import { gridDisk, latLngToCell } from "h3-js";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const webRoot = join(scriptDir, "..");
const bundleConfig = JSON.parse(readFileSync(join(webRoot, "data-bundle.json"), "utf8"));
const bundle = String(process.env.SHIOK_DATA_BUNDLE || bundleConfig.bundle || "").trim();
const dataRoot = join(webRoot, "public", "data", bundle);
const postal = (process.argv[2] || "560234").padStart(6, "0");

function rawPath(rel) {
  return join(dataRoot, rel);
}

function gzPath(rel) {
  return join(dataRoot, `${rel}.gz`);
}

function artifactInfo(rel) {
  if (existsSync(gzPath(rel))) {
    return { rel, encoding: "gzip", bytes: statSync(gzPath(rel)).size, exists: true };
  }
  if (existsSync(rawPath(rel))) {
    if (rel.endsWith(".json")) {
      return {
        rel,
        encoding: "gzip_estimate",
        bytes: gzipSync(readFileSync(rawPath(rel))).length,
        storage_bytes: statSync(rawPath(rel)).size,
        exists: true,
      };
    }
    return { rel, encoding: "identity", bytes: statSync(rawPath(rel)).size, exists: true };
  }
  return { rel, encoding: "missing", bytes: 0, exists: false };
}

function readJson(rel) {
  const info = artifactInfo(rel);
  if (!info.exists) throw new Error(`missing artifact: ${rel}`);
  if (info.encoding === "gzip") {
    return JSON.parse(gunzipSync(readFileSync(gzPath(rel))).toString("utf8"));
  }
  return JSON.parse(readFileSync(rawPath(rel), "utf8"));
}

function decodePolyline(str, precision = 5) {
  let index = 0;
  let lat = 0;
  let lng = 0;
  const coordinates = [];
  const factor = 10 ** precision;

  while (index < str.length) {
    let result = 0;
    let shift = 0;
    let byte = null;
    do {
      byte = str.charCodeAt(index++) - 63;
      result |= (byte & 0x1f) << shift;
      shift += 5;
    } while (byte >= 0x20);
    lat += result & 1 ? ~(result >> 1) : result >> 1;

    result = 0;
    shift = 0;
    do {
      byte = str.charCodeAt(index++) - 63;
      result |= (byte & 0x1f) << shift;
      shift += 5;
    } while (byte >= 0x20);
    lng += result & 1 ? ~(result >> 1) : result >> 1;

    coordinates.push([lat / factor, lng / factor]);
  }

  return coordinates;
}

function addRouteCells(cells, encoded) {
  if (!encoded) return;
  for (const [lat, lng] of decodePolyline(encoded)) {
    const cell = latLngToCell(lat, lng, 8);
    for (const nearby of gridDisk(cell, 1)) cells.add(nearby);
  }
}

const prefix = postal.slice(0, 3);
const scorePrefixIndex = readJson("scores/prefix-index.json");
let scoreShard = null;
let score = null;
for (const shard of scorePrefixIndex[prefix] || []) {
  const records = readJson(`scores/${shard}.json`);
  score = records.find((record) => record.postal === postal);
  if (score) {
    scoreShard = shard;
    break;
  }
}

if (!scoreShard || !score) {
  throw new Error(`postal not found in score shards: ${postal}`);
}

const geomLookupRequests = [];
let geomShard = null;
const prefixIndexInfo = artifactInfo(`geom/postal-prefix/${prefix}.json`);
if (prefixIndexInfo.exists) {
  geomLookupRequests.push(`geom/postal-prefix/${prefix}.json`);
  geomShard = readJson(`geom/postal-prefix/${prefix}.json`)[postal];
} else {
  geomLookupRequests.push(`geom/postal-prefix/${prefix}.json`, "geom/postal-index.json");
  geomShard = readJson("geom/postal-index.json")[postal];
}

if (!geomShard) {
  throw new Error(`postal not found in geometry index: ${postal}`);
}

const geom = readJson(`geom/h3/${geomShard}.json`).find((record) => record.postal === postal);
if (!geom) {
  throw new Error(`postal not found in geometry shard: ${postal}`);
}

const transitCells = new Set();
for (const encoded of geom.shortest_parts?.length ? geom.shortest_parts : [geom.shortest]) {
  addRouteCells(transitCells, encoded);
}
for (const encoded of geom.sheltered_parts?.length ? geom.sheltered_parts : [geom.sheltered]) {
  addRouteCells(transitCells, encoded);
}
for (const gap of geom.exposure_gaps || []) {
  addRouteCells(transitCells, gap.geom);
}

const lookupRequests = [
  "manifest.json",
  "scores/prefix-index.json",
  `scores/${scoreShard}.json`,
  ...geomLookupRequests,
  `geom/h3/${geomShard}.json`,
  ...Array.from(transitCells)
    .sort()
    .map((cell) => `transit/h3/${cell}.json`)
    .filter((rel) => artifactInfo(rel).exists),
];

const uniqueLookup = Array.from(new Set(lookupRequests));
const rows = uniqueLookup.map(artifactInfo);
const lookupBytes = rows.reduce((total, row) => total + row.bytes, 0);
const initialMapTransit = artifactInfo("transit/pois.json");

console.log(
  JSON.stringify(
    {
      bundle,
      postal,
      state: score.state,
      score_shard: scoreShard,
      geom_shard: geomShard,
      lookup_request_count: rows.length,
      lookup_bytes: lookupBytes,
      lookup_kb: Math.round(lookupBytes / 102.4) / 10,
      lookup_budget_bytes: 512000,
      lookup_under_500kb: lookupBytes <= 512000,
      initial_map_transit_bytes: initialMapTransit.bytes,
      initial_map_transit_kb: Math.round(initialMapTransit.bytes / 102.4) / 10,
      rows,
    },
    null,
    2
  )
);
