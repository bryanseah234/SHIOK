import { NextRequest, NextResponse } from "next/server";

// Token cache state
let cachedToken: string | null = null;
let tokenExpiresAt: number = 0;

// Simple in-memory rate limiting map: IP -> { count, windowStart }
const ipThrottleMap = new Map<string, { count: number; windowStart: number }>();
const MAX_REQ_PER_MINUTE = 30;

async function getOneMapToken(): Promise<string | null> {
  const now = Date.now();
  if (cachedToken && now < tokenExpiresAt) {
    return cachedToken;
  }

  const email = process.env.ONEMAP_EMAIL;
  const password = process.env.ONEMAP_PASSWORD;

  if (!email || !password) {
    console.warn("ONEMAP_EMAIL or ONEMAP_PASSWORD not set; proceeding with unauthenticated search.");
    return null;
  }

  try {
    const res = await fetch("https://www.onemap.gov.sg/api/auth/post/getToken", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });

    if (!res.ok) {
      console.error("OneMap auth failed:", res.status);
      return null;
    }

    const data = await res.json();
    if (data.access_token) {
      cachedToken = data.access_token;
      // Expire in 2.8 days (~241920 seconds) to be safe before 3-day hard limit
      tokenExpiresAt = Date.now() + 241920 * 1000;
      return cachedToken;
    }
  } catch (err) {
    console.error("Error fetching OneMap token:", err);
  }

  return null;
}

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const searchVal = searchParams.get("searchVal");

  if (!searchVal) {
    return NextResponse.json({ error: "Missing searchVal query parameter" }, { status: 400 });
  }

  // Rate limiting check per IP
  const ip = request.headers.get("x-forwarded-for") || "127.0.0.1";
  const now = Date.now();
  const throttleRecord = ipThrottleMap.get(ip);

  if (throttleRecord) {
    if (now - throttleRecord.windowStart < 60000) {
      if (throttleRecord.count >= MAX_REQ_PER_MINUTE) {
        return NextResponse.json(
          { error: "Too Many Requests. Rate limit exceeded (30 req/min)." },
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
  const searchUrl = `https://www.onemap.gov.sg/api/common/elastic/search?searchVal=${encodeURIComponent(
    searchVal
  )}&returnGeom=Y&getAddrDetails=Y`;

  const headers: Record<string, string> = {};
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  try {
    let response = await fetch(searchUrl, { headers });

    // Handle token 401 expiry
    if (response.status === 401 && token) {
      cachedToken = null;
      tokenExpiresAt = 0;
      const newToken = await getOneMapToken();
      if (newToken) {
        headers["Authorization"] = `Bearer ${newToken}`;
        response = await fetch(searchUrl, { headers });
      }
    }

    if (!response.ok) {
      return NextResponse.json(
        { error: `OneMap upstream error: ${response.statusText}` },
        { status: response.status }
      );
    }

    const data = await response.json();
    const results = (data.results || []).slice(0, 5);

    return NextResponse.json({
      found: data.found || 0,
      results,
    });
  } catch (err) {
    console.error("Error proxying OneMap search:", err);
    return NextResponse.json({ error: "Failed to query OneMap search API" }, { status: 500 });
  }
}
