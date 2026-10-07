const ID_RE = /^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$/;
const CATEGORY_RE = /^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$/;
const MAX_ICON_BYTES = 256 * 1024;
const MAX_ICON_DIMENSION = 1024;
const DEFAULT_MAX_BUNDLE = 10 * 1024 * 1024;
const RESERVED_IDS = new Set(["appmart", "builder", "settings"]);
const MAX_ZIP_FILES = 256;
const MAX_ZIP_ENTRY_BYTES = 10 * 1024 * 1024;
const MAX_ZIP_EXPANDED_BYTES = 32 * 1024 * 1024;
const RATE_LIMIT_WINDOW_MS = 60 * 60 * 1000;
const RATE_LIMIT_MAX = 10;
const MAX_PENDING_QUEUE = 200;
const STALE_PENDING_MS = 30 * 24 * 60 * 60 * 1000;

function json(data, status = 200, extra = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8",
      "cache-control": status === 200 ? "public, max-age=30" : "no-store",
      ...extra },
  });
}

function error(message, status = 400) {
  return json({ error: message }, status, { "cache-control": "no-store" });
}

function safeEqual(left, right) {
  if (typeof left !== "string" || typeof right !== "string" || !left || !right) {
    return false;
  }
  const a = new TextEncoder().encode(left);
  const b = new TextEncoder().encode(right);
  let difference = a.length ^ b.length;
  const length = Math.max(a.length, b.length);
  for (let i = 0; i < length; i += 1) {
    difference |= (a[i % a.length] || 0) ^ (b[i % b.length] || 0);
  }
  return difference === 0;
}

function authorized(request, token) {
  const value = request.headers.get("authorization") || "";
  return value.startsWith("Bearer ") && safeEqual(value.slice(7), token);
}

function validText(value, max, label, { allowEmpty = false } = {}) {
  if (typeof value !== "string") throw new Error(`${label} must be text`);
  const result = value.trim();
  if ((!allowEmpty && !result) || result.length > max) {
    throw new Error(`${label} must be ${allowEmpty ? "at most" : "1 to"} ${max} characters`);
  }
  return result;
}

function validAppId(value) {
  if (typeof value !== "string" || !ID_RE.test(value)) {
    throw new Error("App ID must use lowercase letters, numbers, and hyphens");
  }
  return value;
}

function parseManifest(text) {
  let manifest;
  try { manifest = JSON.parse(text); } catch { throw new Error("Manifest is not valid JSON"); }
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) {
    throw new Error("Manifest must be a JSON object");
  }
  const id = validAppId(manifest.id);
  if (RESERVED_IDS.has(id)) throw new Error("This app ID is reserved by the system");
  const name = validText(manifest.name, 40, "App name");
  const description = validText(manifest.description || "A MuseBoy community app", 240,
                                "Description");
  const category = validText(manifest.category || "other", 32, "Category").toLowerCase();
  if (!CATEGORY_RE.test(category)) throw new Error("Category must be a lowercase slug");
  const author = validText(manifest.author || "Community", 48, "Author");
  const version = Number.isSafeInteger(manifest.version) && manifest.version > 0
    ? manifest.version : 1;
  if (version > 1000000) throw new Error("Version is out of range");
  return { id, name, description, category, author, version };
}

async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest),
    value => value.toString(16).padStart(2, "0")).join("");
}

function posixNormpath(path) {
  const out = [];
  for (const part of path.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") out.pop();
    else out.push(part);
  }
  return out.join("/");
}

function unsafeZipEntryName(name) {
  if (typeof name !== "string" || !name) return true;
  if (name.startsWith("/") || name.startsWith("\\")) return true;
  if (name.includes("\\") || name.includes(":")) return true;
  if (name.split("/").some(segment => segment === "..")) return true;
  const normalized = posixNormpath(name);
  return normalized === "" || normalized === "." || normalized === ".."
    || normalized.startsWith("../");
}

// Walk the ZIP end-of-central-directory record and central headers with
// DataView (no extraction libraries in the worker). Returns entry metadata.
function parseZipCentralDirectory(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const n = bytes.length;
  let eocd = -1;
  const searchStart = Math.max(0, n - (22 + 65535));
  for (let i = n - 22; i >= searchStart; i--) {
    if (view.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error("Bundle is not a valid ZIP archive");
  const count = view.getUint16(eocd + 10, true);
  const cdOffset = view.getUint32(eocd + 16, true);
  if (cdOffset >= n) throw new Error("Bundle ZIP central directory is corrupt");
  const entries = [];
  let p = cdOffset;
  for (let i = 0; i < count; i++) {
    if (p + 46 > n || view.getUint32(p, true) !== 0x02014b50) {
      throw new Error("Bundle ZIP central directory is corrupt");
    }
    const flags = view.getUint16(p + 8, true);
    const method = view.getUint16(p + 10, true);
    const uncompressed = view.getUint32(p + 24, true);
    const nameLen = view.getUint16(p + 28, true);
    const extraLen = view.getUint16(p + 30, true);
    const commentLen = view.getUint16(p + 32, true);
    const extAttr = view.getUint32(p + 38, true);
    const localOffset = view.getUint32(p + 42, true);
    if (p + 46 + nameLen > n) throw new Error("Bundle ZIP central directory is corrupt");
    const name = new TextDecoder().decode(bytes.subarray(p + 46, p + 46 + nameLen));
    entries.push({ name, method, flags, uncompressed, localOffset, extAttr });
    p += 46 + nameLen + extraLen + commentLen;
  }
  return entries;
}

// Mirror of the device installer's rules (apps/appmart/main.py install_app).
function validateZipEntries(entries) {
  if (!entries.length) throw new Error("Bundle ZIP has no files");
  if (entries.length > MAX_ZIP_FILES) {
    throw new Error(`Bundle has too many files (max ${MAX_ZIP_FILES})`);
  }
  let total = 0;
  let hasAppJson = false;
  let hasMainPy = false;
  for (const entry of entries) {
    if (unsafeZipEntryName(entry.name)) {
      throw new Error(`Bundle contains an unsafe path: ${entry.name}`);
    }
    if (((entry.extAttr >>> 16) & 0o170000) === 0o120000) {
      throw new Error(`Bundle contains a symlink: ${entry.name}`);
    }
    if ((entry.flags & 0x08) && entry.uncompressed === 0) {
      throw new Error("Bundle uses unsupported ZIP data descriptors");
    }
    if (entry.uncompressed > MAX_ZIP_ENTRY_BYTES) {
      throw new Error(`Bundle entry too large: ${entry.name}`);
    }
    total += entry.uncompressed;
    if (total > MAX_ZIP_EXPANDED_BYTES) {
      throw new Error("Bundle expands beyond 32 MiB (possible zip bomb)");
    }
    if (entry.name === "app.json") hasAppJson = true;
    if (entry.name === "main.py") hasMainPy = true;
  }
  if (!hasAppJson) throw new Error("Bundle is missing app.json");
  if (!hasMainPy) throw new Error("Bundle is missing main.py");
}

async function extractZipEntry(bytes, entry) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const offset = entry.localOffset;
  if (view.getUint32(offset, true) !== 0x04034b50) {
    throw new Error(`Bundle ZIP entry is corrupt: ${entry.name}`);
  }
  const nameLen = view.getUint16(offset + 26, true);
  const extraLen = view.getUint16(offset + 28, true);
  const compSize = view.getUint32(offset + 18, true);
  const dataStart = offset + 30 + nameLen + extraLen;
  const data = bytes.subarray(dataStart, dataStart + compSize);
  if (entry.method === 0) return data;
  if (entry.method === 8) {
    const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }
  throw new Error(`Bundle entry uses unsupported compression: ${entry.name}`);
}

// Egregious patterns: hard rejection. Everything else suspicious is a flag
// for the human reviewer, never a block (requests/urllib/socket are legit).
const HARD_BLOCK_PATTERNS = [
  [/\/var\/lib\/musegadget\/sdk_token/, "reads the Muse SDK token"],
  [/\.ssh\//, "touches SSH keys"],
  [/\/etc\/shadow/, "touches /etc/shadow"],
  [/\/etc\/passwd/, "touches /etc/passwd"],
  [/rm\s+-rf?\s+(\/|~)/, "destructive rm -rf"],
  [/: *\(\) *\{ *: *\| *: *& *\} *; *:/, "fork bomb"],
  [/[A-Za-z0-9+/]{4096,}={0,2}/, "base64 blob over 4 KB (likely obfuscated payload)"],
];

const FLAG_PATTERNS = [
  [/\beval\s*\(/, "eval()"],
  [/\bexec\s*\(/, "exec()"],
  [/subprocess/, "subprocess module"],
  [/os\.system\s*\(/, "os.system()"],
  [/\bsocket\b/, "socket module"],
  [/\burllib\b/, "urllib module"],
  [/\/etc\/gpi\/appmart\.json/, "reads the App Mart submit token"],
  [/\bopen\s*\(\s*['"]\//, "opens an absolute path"],
];

function screenMainPy(source) {
  for (const [pattern, label] of HARD_BLOCK_PATTERNS) {
    if (pattern.test(source)) throw new Error(`Blocked: main.py ${label}`);
  }
  const flags = [];
  for (const [pattern, label] of FLAG_PATTERNS) {
    if (pattern.test(source)) flags.push(label);
  }
  return flags;
}

function validatePngIcon(png) {
  if ([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]
      .some((value, index) => png[index] !== value)) {
    throw new Error("Icon is not a PNG image");
  }
  if (png.length < 33) throw new Error("Icon PNG is truncated");
  const view = new DataView(png.buffer, png.byteOffset, png.byteLength);
  const type = String.fromCharCode(png[12], png[13], png[14], png[15]);
  if (type !== "IHDR") throw new Error("Icon PNG has no IHDR chunk");
  const width = view.getUint32(16, false);
  const height = view.getUint32(20, false);
  if (!width || !height || width > MAX_ICON_DIMENSION || height > MAX_ICON_DIMENSION) {
    throw new Error(`Icon PNG dimensions too large (max ${MAX_ICON_DIMENSION}px)`);
  }
}

async function handleSubmission(request, env) {
  if (!authorized(request, env.SUBMIT_TOKEN)) return error("Submission authorization required", 401);
  const tokenHash = await sha256Hex(new TextEncoder().encode(env.SUBMIT_TOKEN || ""));
  const clientIp = request.headers.get("cf-connecting-ip") || "unknown";
  const now = Date.now();
  await env.DB.prepare("DELETE FROM submission_limits WHERE ts < ?")
    .bind(now - RATE_LIMIT_WINDOW_MS).run();
  const used = await env.DB.prepare(`SELECT COUNT(*) AS count FROM submission_limits
    WHERE token_hash = ? AND ip = ? AND ts > ?`)
    .bind(tokenHash, clientIp, now - RATE_LIMIT_WINDOW_MS).first();
  if ((used?.count || 0) >= RATE_LIMIT_MAX) {
    return json({ error: "Submission rate limit exceeded (10 per hour)" }, 429,
      { "cache-control": "no-store", "retry-after": "3600" });
  }
  const pendingRow = await env.DB.prepare(`SELECT COUNT(*) AS count FROM submissions
    WHERE status = 'pending'`).bind().first();
  if ((pendingRow?.count || 0) > MAX_PENDING_QUEUE) {
    return error("Review queue is full, try again later", 503);
  }
  const stale = await env.DB.prepare(`SELECT icon_key, bundle_key FROM submissions
    WHERE status = 'pending' AND submitted_at < ?`)
    .bind(now - STALE_PENDING_MS).all();
  for (const row of stale.results || []) {
    await Promise.all([env.BUCKET.delete(row.icon_key), env.BUCKET.delete(row.bundle_key)]);
  }
  await env.DB.prepare(`DELETE FROM submissions
    WHERE status = 'pending' AND submitted_at < ?`)
    .bind(now - STALE_PENDING_MS).run();
  const maxBundle = Number(env.MAX_APP_BUNDLE_BYTES) || DEFAULT_MAX_BUNDLE;
  const contentLength = Number(request.headers.get("content-length") || 0);
  if (contentLength > maxBundle + MAX_ICON_BYTES + 128 * 1024) {
    return error("Upload exceeds the app package size limit", 413);
  }
  let form;
  try { form = await request.formData(); } catch { return error("Expected a multipart app submission"); }
  let app;
  try { app = parseManifest(form.get("manifest")); } catch (exc) { return error(exc.message); }
  const bundle = form.get("bundle");
  const icon = form.get("icon");
  if (!(bundle instanceof File) || !(icon instanceof File)) {
    return error("Submission needs a ZIP bundle and PNG icon");
  }
  if (!bundle.size || bundle.size > maxBundle) return error("App ZIP is empty or too large", 413);
  if (icon.size < 8 || icon.size > MAX_ICON_BYTES) return error("PNG icon is empty or too large");
  const bundleBytes = await bundle.arrayBuffer();
  const iconBytes = await icon.arrayBuffer();
  const zip = new Uint8Array(bundleBytes);
  const png = new Uint8Array(iconBytes);
  if (zip[0] !== 0x50 || zip[1] !== 0x4b) return error("Bundle is not a ZIP archive");
  let entries;
  try { entries = parseZipCentralDirectory(zip); } catch (exc) { return error(exc.message); }
  try { validateZipEntries(entries); } catch (exc) { return error(exc.message); }
  try { validatePngIcon(png); } catch (exc) { return error(exc.message); }
  let flags = [];
  try {
    const mainPy = entries.find(entry => entry.name === "main.py");
    const mainBytes = await extractZipEntry(zip, mainPy);
    flags = screenMainPy(new TextDecoder("utf-8", { fatal: false }).decode(mainBytes));
  } catch (exc) {
    return error(exc.message);
  }
  const sha256 = await sha256Hex(bundleBytes);
  const id = crypto.randomUUID();
  const iconKey = `pending/${id}/icon.png`;
  const bundleKey = `pending/${id}/app.zip`;
  await env.BUCKET.put(iconKey, iconBytes, { httpMetadata: { contentType: "image/png" } });
  await env.BUCKET.put(bundleKey, bundleBytes, { httpMetadata: { contentType: "application/zip" } });
  try {
    await env.DB.prepare(`INSERT INTO submissions
      (id, app_id, name, description, category, author, version, icon_key,
       bundle_key, sha256, submitted_at, status, flags)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)`)
      .bind(id, app.id, app.name, app.description, app.category, app.author,
            app.version, iconKey, bundleKey, sha256, now,
            flags.length ? JSON.stringify(flags) : null).run();
  } catch (exc) {
    await Promise.all([env.BUCKET.delete(iconKey), env.BUCKET.delete(bundleKey)]);
    return error("Could not queue submission", 503);
  }
  await env.DB.prepare("INSERT INTO submission_limits (token_hash, ip, ts) VALUES (?, ?, ?)")
    .bind(tokenHash, clientIp, now).run();
  return json({ id, status: "pending", message: "App submitted for review" }, 202,
              { "cache-control": "no-store" });
}

async function handleAdmin(request, url, env) {
  if (!authorized(request, env.ADMIN_TOKEN)) return error("Admin authorization required", 401);
  if (url.pathname === "/api/admin/submissions" && request.method === "GET") {
    const result = await env.DB.prepare(`SELECT id, app_id, name, description, category,
      author, version, sha256, submitted_at, flags FROM submissions
      WHERE status = 'pending' ORDER BY submitted_at ASC LIMIT 100`).all();
    return json(result.results || [], 200, { "cache-control": "no-store" });
  }
  const reviewDownload = url.pathname.match(/^\/api\/admin\/submissions\/([0-9a-f-]{36})\/download$/);
  if (reviewDownload && request.method === "GET") {
    const row = await env.DB.prepare(`SELECT * FROM submissions
      WHERE id = ? AND status = 'pending'`).bind(reviewDownload[1]).first();
    if (!row) return error("Pending submission not found", 404);
    const object = await env.BUCKET.get(row.bundle_key);
    if (!object) return error("Submission package is missing", 404);
    return new Response(object.body, { headers: {
      "content-type": "application/zip", "content-length": String(object.size),
      "content-disposition": `attachment; filename=\"${row.app_id}-review.zip\"`,
      "cache-control": "no-store", "x-app-sha256": row.sha256,
    } });
  }
  const match = url.pathname.match(/^\/api\/admin\/submissions\/([0-9a-f-]{36})\/(approve|reject)$/);
  if (!match || request.method !== "POST") return error("Not found", 404);
  const [, submissionId, action] = match;
  const row = await env.DB.prepare(`SELECT * FROM submissions
      WHERE id = ? AND status = 'pending'`).bind(submissionId).first();
  if (!row) return error("Pending submission not found", 404);
  if (action === "reject") {
    await env.DB.prepare("UPDATE submissions SET status = 'rejected' WHERE id = ?")
      .bind(submissionId).run();
    await Promise.all([env.BUCKET.delete(row.icon_key), env.BUCKET.delete(row.bundle_key)]);
    return json({ id: submissionId, status: "rejected" }, 200,
                { "cache-control": "no-store" });
  }
  const icon = await env.BUCKET.get(row.icon_key);
  const bundle = await env.BUCKET.get(row.bundle_key);
  if (!icon || !bundle) return error("Submission files are missing", 503);
  const existing = await env.DB.prepare("SELECT version FROM apps WHERE id = ?")
    .bind(row.app_id).first();
  if (existing && row.version <= existing.version) {
    return error(`Version ${row.version} is not newer than published v${existing.version}`, 400);
  }
  const publishedIconKey = `apps/${row.app_id}/${submissionId}/icon.png`;
  const publishedBundleKey = `apps/${row.app_id}/${submissionId}/app.zip`;
  await env.BUCKET.put(publishedIconKey, icon.body, {
    httpMetadata: { contentType: "image/png" },
  });
  await env.BUCKET.put(publishedBundleKey, bundle.body, {
    httpMetadata: { contentType: "application/zip" },
  });
  try {
    await env.DB.prepare(`INSERT INTO apps
      (id, name, description, category, author, version, icon_key, bundle_key,
       sha256, downloads, published_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
      ON CONFLICT(id) DO UPDATE SET name=excluded.name,
        description=excluded.description, category=excluded.category,
        author=excluded.author, version=excluded.version,
        icon_key=excluded.icon_key, bundle_key=excluded.bundle_key,
        sha256=excluded.sha256, published_at=excluded.published_at`)
      .bind(row.app_id, row.name, row.description, row.category, row.author,
            row.version, publishedIconKey, publishedBundleKey, row.sha256,
            Date.now()).run();
    await env.DB.prepare("UPDATE submissions SET status = 'published' WHERE id = ?")
      .bind(submissionId).run();
  } catch {
    return error("Could not publish submission", 503);
  }
  await Promise.all([env.BUCKET.delete(row.icon_key), env.BUCKET.delete(row.bundle_key)]);
  return json({ id: submissionId, app_id: row.app_id, status: "published" }, 200,
              { "cache-control": "no-store" });
}

async function handleApi(request, env) {
  const url = new URL(request.url);
  if (url.pathname === "/api/health" && request.method === "GET") {
    return json({ ok: true, service: "MuseBoy App Mart" });
  }
  if (url.pathname === "/api/categories" && request.method === "GET") {
    const result = await env.DB.prepare(`SELECT category, COUNT(*) AS app_count
      FROM apps GROUP BY category ORDER BY category`).all();
    return json(result.results || [], 200, { "cache-control": "no-store" });
  }
  if (url.pathname === "/api/apps" && request.method === "GET") {
    const query = (url.searchParams.get("q") || "").trim().slice(0, 80);
    const category = (url.searchParams.get("category") || "").trim().toLowerCase().slice(0, 32);
    if (category && !CATEGORY_RE.test(category)) return error("Invalid category filter");
    const result = await env.DB.prepare(`SELECT id, name, description, category, author,
      version, downloads FROM apps WHERE (? = '' OR category = ?)
      AND (? = '' OR instr(lower(name || ' ' || description || ' ' || author), lower(?)) > 0)
      ORDER BY downloads DESC, name COLLATE NOCASE ASC LIMIT 100`)
      .bind(category, category, query, query).all();
    return json(result.results || [], 200, { "cache-control": "no-store" });
  }
  const asset = url.pathname.match(/^\/api\/apps\/([a-z0-9-]+)\/(icon|download)$/);
  if (asset && request.method === "GET") {
    const [, appId, kind] = asset;
    if (!ID_RE.test(appId)) return error("Invalid app ID");
    const row = await env.DB.prepare("SELECT * FROM apps WHERE id = ?")
      .bind(appId).first();
    if (!row) return error("App not found", 404);
    const key = kind === "icon" ? row.icon_key : row.bundle_key;
    const object = await env.BUCKET.get(key);
    if (!object) return error("App asset not found", 404);
    if (kind === "download") {
      await env.DB.prepare("UPDATE apps SET downloads = downloads + 1 WHERE id = ?")
        .bind(appId).run();
      return new Response(object.body, { headers: {
        "content-type": "application/zip",
        "content-length": String(object.size),
        "content-disposition": `attachment; filename=\"${appId}.zip\"`,
        "cache-control": "no-store",
        "x-app-sha256": row.sha256,
      } });
    }
    return new Response(object.body, { headers: {
      "content-type": "image/png", "content-length": String(object.size),
      "cache-control": "public, max-age=3600",
    } });
  }
  if (url.pathname === "/api/submissions" && request.method === "POST") {
    return handleSubmission(request, env);
  }
  if (url.pathname.startsWith("/api/admin/")) return handleAdmin(request, url, env);
  return error("Not found", 404);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (url.pathname.startsWith("/api/")) return await handleApi(request, env);
      return env.ASSETS.fetch(request);
    } catch {
      return error("App Mart is temporarily unavailable", 503);
    }
  },
};
