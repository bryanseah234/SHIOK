import { spawn } from "node:child_process";
import { existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import http from "node:http";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(scriptDir, "..");
const repoRoot = resolve(webRoot, "..");
const TRANSIT_MODE_LABELS = {
  best_transit: "Best transit",
  mrt_lrt: "MRT/LRT",
  bus: "Bus",
};
const ROUTE_MODE_LABELS = {
  shiokest: "Shiokest",
  both: "Both",
  shortest: "Shortest",
};

function normalizePostalValue(value) {
  const postal = String(value).trim().padStart(6, "0");
  if (!/^\d{6}$/.test(postal)) {
    throw new Error(`postal must be six digits: ${postal}`);
  }
  return postal;
}

function parsePostalList(value) {
  const postals = String(value)
    .split(/[,\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .map(normalizePostalValue);
  if (postals.length === 0) {
    throw new Error("postals list is empty");
  }
  return postals;
}

function parseArgs(argv) {
  const args = {
    url: process.env.SHIOK_BROWSER_QA_URL || "http://127.0.0.1:3000/",
    postal: "",
    postals: [],
    out: "",
    screenshots: false,
    debugPort: 9224,
    chrome: process.env.CHROME_PATH || "",
    timeoutMs: 30000,
    inputMode: "keyboard",
    expectedState: "scored",
    transitMode: "best_transit",
    routeMode: "shiokest",
    mustInclude: [],
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const next = () => {
      index += 1;
      if (index >= argv.length) throw new Error(`missing value for ${arg}`);
      return argv[index];
    };

    if (arg === "--url") args.url = next();
    else if (arg === "--postal") args.postals = [normalizePostalValue(next())];
    else if (arg === "--postals") args.postals = parsePostalList(next());
    else if (arg === "--out") args.out = next();
    else if (arg === "--debug-port") args.debugPort = Number(next());
    else if (arg === "--chrome") args.chrome = next();
    else if (arg === "--timeout-ms") args.timeoutMs = Number(next());
    else if (arg === "--input-mode") args.inputMode = next();
    else if (arg === "--expected-state") args.expectedState = next();
    else if (arg === "--transit-mode") args.transitMode = next();
    else if (arg === "--route-mode") args.routeMode = next();
    else if (arg === "--must-include") args.mustInclude.push(next());
    else if (arg === "--screenshots") args.screenshots = true;
    else if (arg === "--no-screenshots") args.screenshots = false;
    else throw new Error(`unknown arg: ${arg}`);
  }

  if (args.postals.length === 0) {
    args.postals = [normalizePostalValue("560234")];
  }
  args.postal = args.postals[0];
  if (!Number.isInteger(args.debugPort) || args.debugPort < 1) {
    throw new Error(`invalid debug port: ${args.debugPort}`);
  }
  if (!Number.isInteger(args.timeoutMs) || args.timeoutMs < 1000) {
    throw new Error(`invalid timeout: ${args.timeoutMs}`);
  }
  if (!["keyboard", "programmatic"].includes(args.inputMode)) {
    throw new Error(`invalid input mode: ${args.inputMode}`);
  }
  if (!["scored", "no_transit", "not_yet_scored", "any"].includes(args.expectedState)) {
    throw new Error(`invalid expected state: ${args.expectedState}`);
  }
  if (!Object.prototype.hasOwnProperty.call(TRANSIT_MODE_LABELS, args.transitMode)) {
    throw new Error(`invalid transit mode: ${args.transitMode}`);
  }
  if (!Object.prototype.hasOwnProperty.call(ROUTE_MODE_LABELS, args.routeMode)) {
    throw new Error(`invalid route mode: ${args.routeMode}`);
  }
  if (!args.out) {
    const suffix = args.postals.length === 1 ? args.postal : `${args.postals[0]}_plus_${args.postals.length - 1}`;
    args.out = join(repoRoot, "qa", `browser_smoke_${suffix}.json`);
  }
  return args;
}

function candidateChromePaths() {
  if (process.platform === "win32") {
    return [
      process.env.CHROME_PATH,
      join(process.env.ProgramFiles || "", "Google", "Chrome", "Application", "chrome.exe"),
      join(process.env["ProgramFiles(x86)"] || "", "Google", "Chrome", "Application", "chrome.exe"),
      join(process.env.LOCALAPPDATA || "", "Google", "Chrome", "Application", "chrome.exe"),
    ].filter(Boolean);
  }
  if (process.platform === "darwin") {
    return [
      process.env.CHROME_PATH,
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ].filter(Boolean);
  }
  return [process.env.CHROME_PATH, "google-chrome", "chromium", "chromium-browser"].filter(Boolean);
}

function resolveChrome(explicitPath) {
  if (explicitPath) return explicitPath;
  const found = candidateChromePaths().find((candidate) => existsSync(candidate) || !candidate.includes("/") && !candidate.includes("\\"));
  if (!found) throw new Error("Chrome not found. Set CHROME_PATH or pass --chrome.");
  return found;
}

function httpJson(url, method = "GET") {
  return new Promise((resolvePromise, rejectPromise) => {
    const req = http.request(url, { method }, (res) => {
      let data = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => {
        data += chunk;
      });
      res.on("end", () => {
        if ((res.statusCode || 0) < 200 || (res.statusCode || 0) >= 300) {
          rejectPromise(new Error(`${method} ${url} failed ${res.statusCode}: ${data.slice(0, 200)}`));
          return;
        }
        resolvePromise(data ? JSON.parse(data) : {});
      });
    });
    req.on("error", rejectPromise);
    req.end();
  });
}

async function waitForDebugEndpoint(debugBase, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      return await httpJson(`${debugBase}/json/version`);
    } catch {
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));
    }
  }
  throw new Error(`Chrome debug endpoint not ready: ${debugBase}`);
}

class CdpClient {
  constructor(wsUrl) {
    this.nextId = 1;
    this.pending = new Map();
    this.events = [];
    this.ws = new WebSocket(wsUrl);
  }

  async open() {
    await new Promise((resolvePromise, rejectPromise) => {
      this.ws.addEventListener("open", resolvePromise, { once: true });
      this.ws.addEventListener("error", rejectPromise, { once: true });
    });
    this.ws.addEventListener("message", (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolvePromise, rejectPromise } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) rejectPromise(new Error(JSON.stringify(msg.error)));
        else resolvePromise(msg.result || {});
      } else if (
        ["Runtime.exceptionThrown", "Runtime.consoleAPICalled", "Log.entryAdded"].includes(msg.method)
      ) {
        this.events.push(msg);
        this.events = this.events.slice(-20);
      }
    });
  }

  send(method, params = {}) {
    const id = this.nextId;
    this.nextId += 1;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolvePromise, rejectPromise) => {
      this.pending.set(id, { resolvePromise, rejectPromise });
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          rejectPromise(new Error(`CDP timeout: ${method}`));
        }
      }, 30000);
    });
  }

  close() {
    this.ws.close();
  }
}

async function waitForExpression(cdp, expression, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = await cdp.send("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (result.result?.value) return;
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 300));
  }
  const diagnostics = await cdp.send("Runtime.evaluate", {
    expression: `(() => ({
      href: location.href,
      readyState: document.readyState,
      title: document.title,
      bodyText: (document.body?.innerText || "").slice(0, 500),
      bodyHtml: (document.body?.innerHTML || "").slice(0, 500),
    }))()`,
    returnByValue: true,
  });
  throw new Error(
    `timed out waiting for expression: ${expression}; page=${JSON.stringify(diagnostics.result?.value || {})}; events=${JSON.stringify(cdp.events)}`
  );
}

async function pressEnter(cdp) {
  const keyEvent = {
    key: "Enter",
    code: "Enter",
    windowsVirtualKeyCode: 13,
    nativeVirtualKeyCode: 13,
  };
  await cdp.send("Input.dispatchKeyEvent", { ...keyEvent, type: "rawKeyDown" });
  await cdp.send("Input.dispatchKeyEvent", {
    ...keyEvent,
    type: "char",
    text: "\r",
    unmodifiedText: "\r",
  });
  await cdp.send("Input.dispatchKeyEvent", { ...keyEvent, type: "keyUp" });
}

async function clickSearchInput(cdp, timeoutMs) {
  await waitForExpression(cdp, "Boolean(document.querySelector('#postal-search-input'))", timeoutMs);
  const result = await cdp.send("Runtime.evaluate", {
    returnByValue: true,
    awaitPromise: true,
    expression: `(() => {
      const input = document.querySelector('#postal-search-input');
      const rect = input.getBoundingClientRect();
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    })()`,
  });
  const point = result.result?.value;
  if (!point || typeof point.x !== "number" || typeof point.y !== "number") {
    throw new Error("could not resolve postal input click target");
  }
  await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: point.x, y: point.y });
  await cdp.send("Input.dispatchMouseEvent", { type: "mousePressed", x: point.x, y: point.y, button: "left", clickCount: 1 });
  await cdp.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: point.x, y: point.y, button: "left", clickCount: 1 });
  await waitForExpression(cdp, "document.activeElement === document.querySelector('#postal-search-input')", timeoutMs);
}

async function typeText(cdp, text) {
  for (const character of text) {
    const keyEvent = {
      key: character,
      code: `Digit${character}`,
      windowsVirtualKeyCode: character.charCodeAt(0),
      nativeVirtualKeyCode: character.charCodeAt(0),
    };
    await cdp.send("Input.dispatchKeyEvent", { ...keyEvent, type: "rawKeyDown" });
    await cdp.send("Input.dispatchKeyEvent", {
      ...keyEvent,
      type: "char",
      text: character,
      unmodifiedText: character,
    });
    await cdp.send("Input.dispatchKeyEvent", { ...keyEvent, type: "keyUp" });
  }
}

async function searchPostalWithKeyboard(cdp, postal, timeoutMs) {
  await clickSearchInput(cdp, timeoutMs);
  await typeText(cdp, postal);
  await waitForExpression(
    cdp,
    `document.querySelector('#postal-search-input')?.value === '${postal}'`,
    timeoutMs
  );
  await new Promise((resolvePromise) => setTimeout(resolvePromise, 300));
  await pressEnter(cdp);
}

async function searchPostalProgrammatically(cdp, postal, timeoutMs) {
  await waitForExpression(cdp, "Boolean(document.querySelector('#postal-search-input'))", timeoutMs);
  await cdp.send("Runtime.evaluate", {
    awaitPromise: true,
    expression: `(() => {
      const input = document.querySelector('#postal-search-input');
      const button = document.querySelector('#postal-search-button');
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      setter.call(input, '${postal}');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      button.click();
    })()`,
  });
}

async function searchPostal(cdp, postal, timeoutMs, inputMode) {
  if (inputMode === "keyboard") {
    await searchPostalWithKeyboard(cdp, postal, timeoutMs);
  } else {
    await searchPostalProgrammatically(cdp, postal, timeoutMs);
  }
  await waitForExpression(
    cdp,
    `document.body.innerText.includes('Postal ${postal}')`,
    timeoutMs
  );
  await new Promise((resolvePromise) => setTimeout(resolvePromise, 1500));
}

async function selectTransitMode(cdp, transitMode, timeoutMs) {
  const label = TRANSIT_MODE_LABELS[transitMode];
  if (!label) throw new Error(`invalid transit mode: ${transitMode}`);
  if (transitMode === "best_transit") return;
  await waitForExpression(
    cdp,
    `Array.from(document.querySelectorAll('[aria-label="Transit target"] button')).some((button) => button.textContent?.trim() === '${label}')`,
    timeoutMs
  );
  await cdp.send("Runtime.evaluate", {
    awaitPromise: true,
    expression: `(() => {
      const button = Array.from(document.querySelectorAll('[aria-label="Transit target"] button'))
        .find((item) => item.textContent?.trim() === '${label}');
      button.click();
    })()`,
  });
  await waitForExpression(
    cdp,
    `Array.from(document.querySelectorAll('[aria-label="Transit target"] button')).some((button) => button.textContent?.trim() === '${label}' && button.getAttribute('aria-pressed') === 'true')`,
    timeoutMs
  );
  await new Promise((resolvePromise) => setTimeout(resolvePromise, 700));
}

async function selectRouteMode(cdp, routeMode, timeoutMs) {
  const label = ROUTE_MODE_LABELS[routeMode];
  if (!label) throw new Error(`invalid route mode: ${routeMode}`);
  if (routeMode === "shiokest") return;
  await waitForExpression(
    cdp,
    `Array.from(document.querySelectorAll('[aria-label="Route display"] button')).some((button) => button.textContent?.trim() === '${label}')`,
    timeoutMs
  );
  await cdp.send("Runtime.evaluate", {
    awaitPromise: true,
    expression: `(() => {
      const button = Array.from(document.querySelectorAll('[aria-label="Route display"] button'))
        .find((item) => item.textContent?.trim() === '${label}');
      button.click();
    })()`,
  });
  await waitForExpression(
    cdp,
    `Array.from(document.querySelectorAll('[aria-label="Route display"] button')).some((button) => button.textContent?.trim() === '${label}' && button.getAttribute('aria-pressed') === 'true')`,
    timeoutMs
  );
  await new Promise((resolvePromise) => setTimeout(resolvePromise, 700));
}

async function captureScreenshot(cdp, viewport, file) {
  await cdp.send("Emulation.setDeviceMetricsOverride", {
    width: viewport.width,
    height: viewport.height,
    deviceScaleFactor: viewport.mobile ? 2 : 1,
    mobile: viewport.mobile,
  });
  await new Promise((resolvePromise) => setTimeout(resolvePromise, 700));
  const shot = await cdp.send("Page.captureScreenshot", { format: "png", fromSurface: true });
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, Buffer.from(shot.data, "base64"));
}

async function collectPageSummary(cdp) {
  const result = await cdp.send("Runtime.evaluate", {
    returnByValue: true,
    expression: `(() => {
      const card = document.querySelector('section[aria-label="Score panel"]');
      const map = document.querySelector('[aria-describedby="route-map-summary"]');
      const summary = document.querySelector('#route-map-summary');
      const details = document.querySelector('details');
      const overlay = document.querySelector('[class*=detailOverlay]');
      const activeTransitButton = Array.from(document.querySelectorAll('[aria-label="Transit target"] button'))
        .find((button) => button.getAttribute('aria-pressed') === 'true');
      const activeRouteButton = Array.from(document.querySelectorAll('[aria-label="Route display"] button'))
        .find((button) => button.getAttribute('aria-pressed') === 'true');
      const rect = card?.getBoundingClientRect();
      return {
        cardText: card?.innerText || '',
        mapLabel: map?.getAttribute('aria-label') || '',
        mapSummary: summary?.innerText || '',
        activeTransitMode: activeTransitButton?.textContent?.trim() || '',
        activeRouteMode: activeRouteButton?.textContent?.trim() || '',
        metrics: {
          viewportWidth: innerWidth,
          viewportHeight: innerHeight,
          cardBottom: rect?.bottom ?? null,
          viewportBottom: innerHeight,
          detailsVisible: Boolean(details),
          overlayClientHeight: overlay?.clientHeight ?? null,
          overlayScrollHeight: overlay?.scrollHeight ?? null
        }
      };
    })()`,
  });
  return result.result.value;
}

function collectChecks(summary, postal, inputMode, expectedState, transitMode, routeMode, mustInclude) {
  const hasScore = summary.cardText.includes("/100");
  const hasNoTransit =
    summary.cardText.includes("No routed") ||
    summary.cardText.includes("No transit found nearby") ||
    summary.cardText.includes("No best transit walk was found") ||
    summary.cardText.includes("Transit beyond scoring range") ||
    summary.cardText.includes("Closest routed transit");
  const hasNotYetScored =
    summary.cardText.includes("Not scored") ||
    (summary.cardText.includes("No score") && summary.cardText.includes("needs usable location evidence"));
  const checks = {
    score_panel_loaded: summary.cardText.includes(`Postal ${postal}`),
    pending_badge_absent: !summary.cardText
      .split("\n")
      .some((line) => line.trim().toLowerCase() === "pending"),
    map_has_text_equivalent: Boolean(summary.mapSummary),
    short_mobile_card_bottom_visible:
      typeof summary.metrics.cardBottom === "number" &&
      summary.metrics.cardBottom <= summary.metrics.viewportBottom + 2,
    keyboard_search_used: inputMode === "keyboard",
    transit_mode_selected:
      transitMode === "best_transit" || summary.activeTransitMode === TRANSIT_MODE_LABELS[transitMode],
    route_mode_selected: routeMode === "shiokest" || summary.activeRouteMode === ROUTE_MODE_LABELS[routeMode],
    required_text_present: mustInclude.every((text) => summary.cardText.includes(text) || summary.mapSummary.includes(text)),
  };
  if (expectedState === "scored") {
    return {
      ...checks,
      score_has_max_denominator: hasScore,
      transit_legend_present: summary.cardText.includes("MRT/LRT") && summary.cardText.includes("Bus stop"),
      route_mode_present: summary.cardText.includes("Shiokest") || summary.cardText.includes("Direct bus estimate"),
    };
  }
  if (expectedState === "no_transit") {
    return { ...checks, no_transit_state_present: hasNoTransit };
  }
  if (expectedState === "not_yet_scored") {
    return {
      ...checks,
      not_yet_scored_state_present: hasNotYetScored,
      not_yet_copy_distinct_from_no_transit:
        !summary.cardText.includes("No Transit Found Nearby") && !summary.cardText.includes("No routed transit"),
    };
  }
  return checks;
}

async function runPostalCase(cdp, args, postal, outputDir, shotBase) {
  await cdp.send("Page.navigate", { url: args.url });
  await new Promise((resolvePromise) => setTimeout(resolvePromise, 500));
  await waitForExpression(cdp, "document.readyState === 'complete'", args.timeoutMs);
  await searchPostal(cdp, postal, args.timeoutMs, args.inputMode);
  await selectTransitMode(cdp, args.transitMode, args.timeoutMs);
  await selectRouteMode(cdp, args.routeMode, args.timeoutMs);

  const summary = await collectPageSummary(cdp);
  const screenshots = [];
  const caseShotBase = args.postals.length === 1 ? shotBase : `${shotBase}_${postal}`;

  if (args.screenshots) {
    const desktop = join(outputDir, `${caseShotBase}_desktop.png`);
    const mobile = join(outputDir, `${caseShotBase}_mobile.png`);
    const mobileShort = join(outputDir, `${caseShotBase}_mobile_short.png`);
    await captureScreenshot(cdp, { width: 1440, height: 950, mobile: false }, desktop);
    await captureScreenshot(cdp, { width: 390, height: 844, mobile: true }, mobile);
    await captureScreenshot(cdp, { width: 390, height: 667, mobile: true }, mobileShort);
    screenshots.push(desktop, mobile, mobileShort);
    Object.assign(summary, await collectPageSummary(cdp));
  } else {
    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width: 390,
      height: 667,
      deviceScaleFactor: 2,
      mobile: true,
    });
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 700));
    Object.assign(summary, await collectPageSummary(cdp));
  }

  const checks = collectChecks(
    summary,
    postal,
    args.inputMode,
    args.expectedState,
    args.transitMode,
    args.routeMode,
    args.mustInclude
  );
  return {
    postal,
    input_mode: args.inputMode,
    expected_state: args.expectedState,
    transit_mode: args.transitMode,
    route_mode: args.routeMode,
    must_include: args.mustInclude,
    screenshots,
    score_panel_excerpt: summary.cardText.split("\n").slice(0, 32),
    map_label: summary.mapLabel,
    map_summary: summary.mapSummary,
    active_transit_mode: summary.activeTransitMode,
    active_route_mode: summary.activeRouteMode,
    metrics: summary.metrics,
    checks,
    ok: Object.values(checks).every(Boolean),
  };
}

async function runSmoke(args) {
  const chrome = resolveChrome(args.chrome);
  const debugBase = `http://127.0.0.1:${args.debugPort}`;
  const userDataDir = join(repoRoot, "tmp", `browser-smoke-${process.pid}-${Date.now()}`);
  mkdirSync(userDataDir, { recursive: true });
  const chromeProcess = spawn(
    chrome,
    [
      "--headless=new",
      `--remote-debugging-port=${args.debugPort}`,
      "--disable-gpu",
      "--no-first-run",
      "--no-default-browser-check",
      `--user-data-dir=${userDataDir}`,
      "about:blank",
    ],
    { stdio: "ignore", windowsHide: true }
  );

  let cdp = null;
  try {
    await waitForDebugEndpoint(debugBase, args.timeoutMs);
    const page = await httpJson(`${debugBase}/json/new?${encodeURIComponent(args.url)}`, "PUT");
    cdp = new CdpClient(page.webSocketDebuggerUrl);
    await cdp.open();
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Log.enable");
    await cdp.send("Page.bringToFront");
    const outputDir = dirname(resolve(args.out));
    const shotBase = basename(args.out, ".json");
    const results = [];
    for (const postal of args.postals) {
      results.push(await runPostalCase(cdp, args, postal, outputDir, shotBase));
    }

    const commonPayload = {
      generated_at: new Date().toISOString(),
      url: args.url,
      input_mode: args.inputMode,
      expected_state: args.expectedState,
      transit_mode: args.transitMode,
      route_mode: args.routeMode,
      must_include: args.mustInclude,
    };
    const payload =
      results.length === 1
        ? { ...commonPayload, ...results[0] }
        : {
            ...commonPayload,
            postals: args.postals,
            result_count: results.length,
            results,
            ok: results.every((result) => result.ok),
          };

    mkdirSync(outputDir, { recursive: true });
    writeFileSync(args.out, `${JSON.stringify(payload, null, 2)}\n`);
    console.log(JSON.stringify(payload, null, 2));

    if (!payload.ok) {
      process.exitCode = 1;
    }
  } finally {
    if (cdp) cdp.close();
    chromeProcess.kill();
    try {
      rmSync(userDataDir, { recursive: true, force: true });
    } catch {
      // Temp cleanup failure is not a browser QA failure.
    }
  }
}

try {
  const args = parseArgs(process.argv.slice(2));
  await runSmoke(args);
} catch (error) {
  console.error(error.stack || error.message);
  process.exit(1);
}
