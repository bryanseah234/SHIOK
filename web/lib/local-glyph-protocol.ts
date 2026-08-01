import TinySDF from "@mapbox/tiny-sdf";
import type { AddProtocolAction } from "maplibre-gl";
import { PbfWriter } from "pbf";

interface Glyph {
  id: number;
  data: Uint8Array;
  width: number;
  height: number;
  glyphTop: number;
  glyphLeft: number;
  glyphAdvance: number;
}

const GLYPH_BUFFER = 3;
const glyphCache = new Map<string, ArrayBuffer>();

function writeFontstacks(glyphs: Glyph[], pbf: PbfWriter) {
  pbf.writeMessage(1, writeFontstack, glyphs);
}

function writeFontstack(glyphs: Glyph[], pbf: PbfWriter) {
  for (const glyph of glyphs) {
    pbf.writeMessage(3, writeGlyph, glyph);
  }
}

function writeGlyph(glyph: Glyph, pbf: PbfWriter) {
  pbf.writeVarintField(1, glyph.id);
  pbf.writeBytesField(2, glyph.data);
  pbf.writeVarintField(3, Math.max(0, glyph.width - 2 * GLYPH_BUFFER));
  pbf.writeVarintField(4, Math.max(0, glyph.height - 2 * GLYPH_BUFFER));
  pbf.writeSVarintField(5, glyph.glyphLeft);
  pbf.writeSVarintField(6, glyph.glyphTop);
  pbf.writeVarintField(7, glyph.glyphAdvance);
}

function parseRange(range: string): [number, number] {
  const [startRaw, endRaw] = range.split("-");
  const start = Number(startRaw);
  const end = Number(endRaw);
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end < start) {
    throw new Error(`Invalid glyph range: ${range}`);
  }
  return [start, end];
}

function arrayBufferFor(data: Uint8Array): ArrayBuffer {
  const buffer = new ArrayBuffer(data.byteLength);
  new Uint8Array(buffer).set(data);
  return buffer;
}

function generateGlyphs(fontstack: string, range: string): ArrayBuffer {
  const [start, end] = parseRange(range);
  const tinySdf = new TinySDF({
    fontSize: 24,
    fontFamily: "Arial",
    fontWeight: /(?:bold|medium|semibold)/i.test(fontstack) ? "600" : "normal",
    fontStyle: /italic/i.test(fontstack) ? "italic" : "normal",
    buffer: GLYPH_BUFFER,
    radius: 8,
    cutoff: 0.25,
  });
  const pbf = new PbfWriter();
  const glyphs: Glyph[] = [];

  for (let id = start; id <= end + 1; id += 1) {
    const drawn = tinySdf.draw(String.fromCharCode(id));
    glyphs.push({
      id,
      data: new Uint8Array(drawn.data.buffer, drawn.data.byteOffset, drawn.data.byteLength),
      width: drawn.width,
      height: drawn.height,
      glyphTop: drawn.glyphTop,
      glyphLeft: drawn.glyphLeft,
      glyphAdvance: drawn.glyphAdvance,
    });
  }

  writeFontstacks(glyphs, pbf);
  return arrayBufferFor(pbf.finish());
}

const localGlyphProtocol: AddProtocolAction = async (params) => {
  const match = params.url.match(/^glyphs:\/\/(.+)\/(\d+-\d+)$/i);
  if (!match) {
    throw new Error(`Invalid local glyph URL: ${params.url}`);
  }
  const [, fontstack, range] = match;
  const cacheKey = `${fontstack}/${range}`;
  let data = glyphCache.get(cacheKey);
  if (!data) {
    data = generateGlyphs(decodeURIComponent(fontstack), range);
    glyphCache.set(cacheKey, data);
  }
  return { data };
};

export default localGlyphProtocol;
