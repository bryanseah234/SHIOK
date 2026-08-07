export type LatLng = [number, number];

export function decodePolyline(encoded: string, precision = 5): LatLng[] {
  const factor = 10 ** precision;
  const points: LatLng[] = [];
  let index = 0;
  let lat = 0;
  let lng = 0;

  while (index < encoded.length) {
    let result = 0;
    let shift = 0;
    let byte = 0;

    do {
      byte = encoded.charCodeAt(index++) - 63;
      result |= (byte & 0x1f) << shift;
      shift += 5;
    } while (byte >= 0x20 && index < encoded.length);

    const deltaLat = result & 1 ? ~(result >> 1) : result >> 1;
    lat += deltaLat;

    result = 0;
    shift = 0;
    do {
      byte = encoded.charCodeAt(index++) - 63;
      result |= (byte & 0x1f) << shift;
      shift += 5;
    } while (byte >= 0x20 && index < encoded.length);

    const deltaLng = result & 1 ? ~(result >> 1) : result >> 1;
    lng += deltaLng;

    points.push([lat / factor, lng / factor]);
  }

  return points;
}

export function encodePolyline(points: LatLng[], precision = 5): string {
  const factor = 10 ** precision;
  let output = "";
  let prevLat = 0;
  let prevLng = 0;

  for (const [lat, lng] of points) {
    const latInt = Math.round(lat * factor);
    const lngInt = Math.round(lng * factor);
    output += encodeSigned(latInt - prevLat);
    output += encodeSigned(lngInt - prevLng);
    prevLat = latInt;
    prevLng = lngInt;
  }
  return output;
}

function encodeSigned(num: number): string {
  let sgnNum = num < 0 ? ~(num << 1) : num << 1;
  let encodeString = "";
  while (sgnNum >= 0x20) {
    encodeString += String.fromCharCode((0x20 | (sgnNum & 0x1f)) + 63);
    sgnNum >>= 5;
  }
  encodeString += String.fromCharCode(sgnNum + 63);
  return encodeString;
}
