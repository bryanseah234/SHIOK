import { NextRequest, NextResponse } from "next/server";

// Token cache state (shared with search or independent)
let cachedToken: string | null = null;
let tokenExpiresAt = 0;

// Simple in-memory rate limiting map: IP -> { count, windowStart }
const ipThrottleMap = new Map<string, { count: number; windowStart: number }>();
const MAX_REQ_PER_MINUTE = 60;

const SINGAPORE_BOUNDS = {
  minLat: 1.15,
  maxLat: 1.48,
  minLng: 103.58,
  maxLng: 104.08,
};

async function getOneMapToken(): Promise<string | null> {
  const now = Date.now();
  if (cachedToken && now < tokenExpiresAt) {
    return cachedToken;
  }

  const email = process.env.ONEMAP_EMAIL;
  const password = process.env.ONEMAP_PASSWORD;

  if (!email || !password) {
    return null;
  }

  try {
    const res = await fetch("https://www.onemap.gov.sg/api/auth/post/getToken", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });

    if (!res.ok) {
      console.error("OneMap auth failed for route proxy:", res.status);
      return null;
    }

    const data = await res.json();
    if (data.access_token) {
      cachedToken = data.access_token;
      tokenExpiresAt = Date.now() + 241920 * 1000;
      return cachedToken;
    }
  } catch (err) {
    console.error("Error fetching OneMap token for route proxy:", err);
  }

  return null;
}

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const startLatStr = searchParams.get("startLat");
  const startLngStr = searchParams.get("startLng");
  const endLatStr = searchParams.get("endLat");
  const endLngStr = searchParams.get("endLng");

  if (!startLatStr || !startLngStr || !endLatStr || !endLngStr) {
    return NextResponse.json(
      { ok: false, error: "Missing required coordinate parameters (startLat, startLng, endLat, endLng)" },
      { status: 400 }
    );
  }

  const startLat = Number(startLatStr);
  const startLng = Number(startLngStr);
  const endLat = Number(endLatStr);
  const endLng = Number(endLngStr);

  if (
    !Number.isFinite(startLat) ||
    !Number.isFinite(startLng) ||
    !Number.isFinite(endLat) ||
    !Number.isFinite(endLng)
  ) {
    return NextResponse.json(
      { ok: false, error: "Coordinates must be valid numbers" },
      { status: 400 }
    );
  }

  // Sanity check coordinates in Singapore
  if (
    startLat < SINGAPORE_BOUNDS.minLat ||
    startLat > SINGAPORE_BOUNDS.maxLat ||
    startLng < SINGAPORE_BOUNDS.minLng ||
    startLng > SINGAPORE_BOUNDS.maxLng ||
    endLat < SINGAPORE_BOUNDS.minLat ||
    endLat > SINGAPORE_BOUNDS.maxLat ||
    endLng < SINGAPORE_BOUNDS.minLng ||
    endLng > SINGAPORE_BOUNDS.maxLng
  ) {
    return NextResponse.json(
      { ok: false, error: "Coordinates outside Singapore bounding box" },
      { status: 400 }
    );
  }

  // Rate limiting check per IP
  const ip = request.headers.get("x-forwarded-for") || "127.0.0.1";
  const now = Date.now();
  const throttleRecord = ipThrottleMap.get(ip);

  if (throttleRecord) {
    if (now - throttleRecord.windowStart < 60000) {
      if (throttleRecord.count >= MAX_REQ_PER_MINUTE) {
        return NextResponse.json(
          { ok: false, error: "Too Many Requests. Rate limit exceeded (60 req/min)." },
          { status: 429, headers: { "Retry-After": "60" } }
        );
      }
      throttleRecord.count += 1;
    } else {
      ipThrottleMap.set(ip, { count: 1, windowStart: now });
    }
  } else {
    ipThrottleMap.set(ip, { count: 1, windowStart: now });
  }

  const token = await getOneMapToken();
  const routeUrl = `https://www.onemap.gov.sg/api/public/routingsvc/route?start=${startLat},${startLng}&end=${endLat},${endLng}&routeType=walk`;

  const headers: Record<string, string> = {};
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  try {
    let response = await fetch(routeUrl, { headers });

    // Handle token 401 expiry
    if (response.status === 401 && token) {
      cachedToken = null;
      tokenExpiresAt = 0;
      const newToken = await getOneMapToken();
      if (newToken) {
        headers["Authorization"] = `Bearer ${newToken}`;
        response = await fetch(routeUrl, { headers });
      }
    }

    if (!response.ok) {
      return NextResponse.json(
        { ok: false, error: `OneMap routing upstream error: ${response.statusText}` },
        { status: response.status }
      );
    }

    const data = await response.json();
    if (!data.route_geometry) {
      return NextResponse.json(
        { ok: false, error: data.status_message || "No route geometry returned by OneMap" },
        { status: 404 }
      );
    }

    const totalDistanceM = data.route_summary?.total_distance ?? 0;
    const totalTimeS = data.route_summary?.total_time ?? 0;

    return NextResponse.json({
      ok: true,
      route_geometry: data.route_geometry,
      total_distance_m: totalDistanceM,
      total_time_s: totalTimeS,
      status_message: data.status_message ?? "Found route",
    });
  } catch (err) {
    console.error("Error proxying OneMap route:", err);
    return NextResponse.json({ ok: false, error: "Failed to query OneMap route API" }, { status: 500 });
  }
}
