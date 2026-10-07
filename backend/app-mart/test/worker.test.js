import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";

class MemoryDB {
  constructor() { this.apps = []; this.submissions = []; this.limits = []; }
  prepare(sql) {
    const db = this;
    const bind = (...values) => ({
        async all() {
          if (sql.includes("SELECT category, COUNT(*)")) {
            const counts = new Map();
            for (const app of db.apps) counts.set(app.category, (counts.get(app.category) || 0) + 1);
            return { results: [...counts].map(([category, app_count]) => ({ category, app_count })) };
          }
          if (sql.includes("FROM submissions")) {
            let rows = db.submissions.filter(item => item.status === "pending");
            if (sql.includes("submitted_at <")) {
              rows = rows.filter(item => item.submitted_at < values[0]);
            }
            return { results: rows };
          }
          const [category, , query] = values;
          return { results: db.apps.filter(app =>
            (!category || app.category === category) &&
            (!query || `${app.name} ${app.description} ${app.author}`.toLowerCase()
              .includes(query.toLowerCase()))) };
        },
        async first() {
          if (sql.includes("FROM submission_limits")) {
            const [tokenHash, ip, since] = values;
            return { count: db.limits.filter(limit =>
              limit.token_hash === tokenHash && limit.ip === ip && limit.ts > since).length };
          }
          if (sql.includes("FROM submissions")) {
            if (sql.includes("COUNT(*)")) {
              return { count: db.submissions.filter(item => item.status === "pending").length };
            }
            const [id] = values;
            return db.submissions.find(item => item.id === id && item.status === "pending") || null;
          }
          if (sql.includes("FROM apps")) {
            const [id] = values;
            return db.apps.find(item => item.id === id) || null;
          }
          return null;
        },
        async run() {
          if (sql.includes("INSERT INTO submissions")) {
            const [id, app_id, name, description, category, author, version,
              icon_key, bundle_key, sha256, submitted_at, flags] = values;
            db.submissions.push({ id, app_id, name, description, category, author,
              version, icon_key, bundle_key, sha256, submitted_at, flags,
              status: "pending" });
          } else if (sql.includes("INSERT INTO submission_limits")) {
            const [token_hash, ip, ts] = values;
            db.limits.push({ token_hash, ip, ts });
          } else if (sql.includes("DELETE FROM submission_limits")) {
            const [before] = values;
            db.limits = db.limits.filter(limit => limit.ts >= before);
          } else if (sql.includes("DELETE FROM submissions")) {
            const [before] = values;
            db.submissions = db.submissions.filter(item =>
              !(item.status === "pending" && item.submitted_at < before));
          } else if (sql.includes("INSERT INTO apps")) {
            const [id, name, description, category, author, version,
              icon_key, bundle_key, sha256, published_at] = values;
            const previous = db.apps.find(app => app.id === id);
            const app = { id, name, description, category, author, version,
              icon_key, bundle_key, sha256, downloads: previous?.downloads || 0,
              published_at };
            if (previous) Object.assign(previous, app);
            else db.apps.push(app);
          } else if (sql.includes("UPDATE submissions SET status = 'published'")) {
            const [id] = values;
            const row = db.submissions.find(item => item.id === id);
            if (row) row.status = "published";
          } else if (sql.includes("UPDATE submissions SET status = 'rejected'")) {
            const [id] = values;
            const row = db.submissions.find(item => item.id === id);
            if (row) row.status = "rejected";
          } else if (sql.includes("UPDATE apps SET downloads")) {
            const [id] = values;
            const row = db.apps.find(item => item.id === id);
            if (row) row.downloads += 1;
          }
          return { success: true };
        },
      });
    return { all: () => bind().all(), bind(...values) {
      return {
        all: () => bind(...values).all(),
        first: () => bind(...values).first(),
        run: () => bind(...values).run(),
      };
    } };
  }
}

function makeEnv() {
  const objects = new Map();
  return {
    DB: new MemoryDB(),
    BUCKET: {
      async put(key, body, options = {}) {
        const bytes = body instanceof ArrayBuffer ? new Uint8Array(body)
          : body instanceof Uint8Array ? body : new Uint8Array(await new Response(body).arrayBuffer());
        objects.set(key, { bytes, contentType: options.httpMetadata?.contentType });
      },
      async get(key) {
        const item = objects.get(key);
        return item ? { body: item.bytes, size: item.bytes.length } : null;
      },
      async delete(key) { objects.delete(key); },
    },
    ASSETS: { fetch: async () => new Response("admin") },
    ADMIN_TOKEN: "admin-test-secret",
    SUBMIT_TOKEN: "submit-test-secret",
  };
}

const call = (path, env, options = {}) => worker.fetch(
  new Request(`https://mart.test${path}`, options), env);

// Build a minimal stored (uncompressed) ZIP for tests.
function makeZip(entries) {
  const enc = new TextEncoder();
  const chunks = [];
  const central = [];
  let offset = 0;
  for (const { name, data, fakeUncompressed } of entries) {
    const nameBytes = enc.encode(name);
    const dataBytes = typeof data === "string" ? enc.encode(data) : data;
    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true);
    local.setUint32(18, dataBytes.length, true);
    local.setUint32(22, dataBytes.length, true);
    local.setUint16(26, nameBytes.length, true);
    chunks.push(new Uint8Array(local.buffer), nameBytes, dataBytes);
    const head = new DataView(new ArrayBuffer(46));
    head.setUint32(0, 0x02014b50, true);
    head.setUint32(24, fakeUncompressed ?? dataBytes.length, true);
    head.setUint16(28, nameBytes.length, true);
    head.setUint32(42, offset, true);
    central.push(new Uint8Array(head.buffer), nameBytes);
    offset += 30 + nameBytes.length + dataBytes.length;
  }
  const cdStart = offset;
  for (const chunk of central) { chunks.push(chunk); offset += chunk.length; }
  const eocd = new DataView(new ArrayBuffer(22));
  eocd.setUint32(0, 0x06054b50, true);
  eocd.setUint16(8, entries.length, true);
  eocd.setUint16(10, entries.length, true);
  eocd.setUint32(12, offset - cdStart, true);
  eocd.setUint32(16, cdStart, true);
  chunks.push(new Uint8Array(eocd.buffer));
  const out = new Uint8Array(chunks.reduce((sum, chunk) => sum + chunk.length, 0));
  let position = 0;
  for (const chunk of chunks) { out.set(chunk, position); position += chunk.length; }
  return out;
}

function makePng(width, height) {
  const png = new Uint8Array(33);
  png.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const view = new DataView(png.buffer);
  view.setUint32(8, 13, false);
  png.set([0x49, 0x48, 0x44, 0x52], 12); // "IHDR"
  view.setUint32(16, width, false);
  view.setUint32(20, height, false);
  return png;
}

const GOOD_MAIN = "import pygame\nprint('hello from a safe app')\n";
const GOOD_ZIP = () => makeZip([
  { name: "app.json", data: JSON.stringify({ id: "x", name: "X" }) },
  { name: "main.py", data: GOOD_MAIN },
]);

function submitForm({ manifest, bundle, icon } = {}) {
  const form = new FormData();
  form.set("manifest", JSON.stringify(manifest || { id: "hello", name: "Hello",
    description: "A demo", category: "demo", author: "Moiz", version: 1 }));
  form.set("bundle", new File([bundle || GOOD_ZIP()], "hello.zip"));
  form.set("icon", new File([icon || makePng(64, 64)], "icon.png"));
  return form;
}

const submit = (env, form) => call("/api/submissions", env, { method: "POST",
  body: form || submitForm(),
  headers: { authorization: "Bearer submit-test-secret" } });

test("health and searchable category-filtered published catalog", async () => {
  const env = makeEnv();
  env.DB.apps.push(
    { id: "hello", name: "Hello", description: "A greeting", category: "demo", author: "MuseBoy", downloads: 0 },
    { id: "blocks", name: "Blocks", description: "A puzzle game", category: "games", author: "MuseBoy", downloads: 3 },
  );
  const health = await call("/api/health", env);
  assert.equal(health.status, 200);
  assert.equal((await health.json()).ok, true);
  const apps = await call("/api/apps?q=puzzle&category=games", env);
  assert.equal(apps.headers.get("cache-control"), "no-store");
  assert.deepEqual((await apps.json()).map(app => app.id), ["blocks"]);
  const categories = await call("/api/categories", env);
  assert.equal(categories.headers.get("cache-control"), "no-store");
  assert.deepEqual((await categories.json()).map(row => row.category).sort(), ["demo", "games"]);
});

test("submission stays private until an admin approves it, then is downloadable", async () => {
  const env = makeEnv();
  const form = submitForm();

  const unauthorized = await call("/api/submissions", env, { method: "POST", body: form });
  assert.equal(unauthorized.status, 401);
  const response = await submit(env, form);
  assert.equal(response.status, 202);
  assert.equal(env.DB.apps.length, 0);
  const { id } = await response.json();

  const pending = await call("/api/admin/submissions", env, {
    headers: { authorization: "Bearer admin-test-secret" },
  });
  assert.equal((await pending.json()).length, 1);
  const approve = await call(`/api/admin/submissions/${id}/approve`, env, {
    method: "POST", headers: { authorization: "Bearer admin-test-secret" },
  });
  assert.equal(approve.status, 200);
  const catalog = await call("/api/apps", env);
  assert.equal((await catalog.json())[0].id, "hello");
  const download = await call("/api/apps/hello/download", env);
  assert.equal(download.status, 200);
  assert.equal(download.headers.get("content-type"), "application/zip");
});

test("rate limit: 11th submission in an hour gets 429 with Retry-After", async () => {
  const env = makeEnv();
  for (let i = 0; i < 10; i++) {
    const response = await submit(env);
    assert.equal(response.status, 202, `submission ${i + 1} should succeed`);
  }
  const limited = await submit(env);
  assert.equal(limited.status, 429);
  assert.equal(limited.headers.get("retry-after"), "3600");
  assert.match((await limited.json()).error, /rate limit/i);
});

test("ZIP traversal entry is rejected with 400", async () => {
  const env = makeEnv();
  const zip = makeZip([
    { name: "app.json", data: "{}" },
    { name: "main.py", data: GOOD_MAIN },
    { name: "../evil.py", data: "evil" },
  ]);
  const response = await submit(env, submitForm({ bundle: zip }));
  assert.equal(response.status, 400);
  assert.match((await response.json()).error, /unsafe path/i);
});

test("zip bomb (central directory claims >32 MiB) is rejected", async () => {
  const env = makeEnv();
  const zip = makeZip([
    { name: "app.json", data: "{}" },
    { name: "main.py", data: GOOD_MAIN },
    { name: "payload.bin", data: new Uint8Array([1, 2, 3]),
      fakeUncompressed: 40 * 1024 * 1024 },
  ]);
  const response = await submit(env, submitForm({ bundle: zip }));
  assert.equal(response.status, 400);
  assert.match((await response.json()).error, /too large|32 MiB/i);
  // aggregate: four 9 MiB entries stay under the per-entry cap but exceed 32 MiB total
  const aggregate = makeZip([
    { name: "app.json", data: "{}" },
    { name: "main.py", data: GOOD_MAIN },
    ...[1, 2, 3, 4].map(i => ({ name: `blob${i}.bin`, data: new Uint8Array([i]),
      fakeUncompressed: 9 * 1024 * 1024 })),
  ]);
  const aggregateResponse = await submit(env, submitForm({ bundle: aggregate }));
  assert.equal(aggregateResponse.status, 400);
  assert.match((await aggregateResponse.json()).error, /32 MiB/);
});

test("bundle missing main.py is rejected", async () => {
  const env = makeEnv();
  const zip = makeZip([{ name: "app.json", data: "{}" }]);
  const response = await submit(env, submitForm({ bundle: zip }));
  assert.equal(response.status, 400);
  assert.match((await response.json()).error, /main\.py/);
});

test("main.py reading the SDK token is hard-blocked", async () => {
  const env = makeEnv();
  const zip = makeZip([
    { name: "app.json", data: "{}" },
    { name: "main.py", data: "token = open('/var/lib/musegadget/sdk_token').read()\n" },
  ]);
  const response = await submit(env, submitForm({ bundle: zip }));
  assert.equal(response.status, 400);
  assert.match((await response.json()).error, /Blocked/i);
});

test("main.py fork bomb is hard-blocked", async () => {
  const env = makeEnv();
  const zip = makeZip([
    { name: "app.json", data: "{}" },
    { name: "main.py", data: "import os\nos.system(':(){ :|:& };:')\n" },
  ]);
  const response = await submit(env, submitForm({ bundle: zip }));
  assert.equal(response.status, 400);
  assert.match((await response.json()).error, /Blocked/i);
});

test("suspicious-but-legit code only sets flags, still accepted", async () => {
  const env = makeEnv();
  const zip = makeZip([
    { name: "app.json", data: "{}" },
    { name: "main.py", data: "import requests\nr = requests.get('https://api.weather.test/x', timeout=10)\nprint(r.text)\n" },
  ]);
  const response = await submit(env, submitForm({ bundle: zip }));
  assert.equal(response.status, 202);
  assert.equal(env.DB.submissions[0].flags, null);
  const flagged = makeZip([
    { name: "app.json", data: "{}" },
    { name: "main.py", data: "import subprocess\nsubprocess.run(['ls'])\n" },
  ]);
  const flaggedResponse = await submit(env, submitForm({
    manifest: { id: "flagged", name: "Flagged", version: 1 }, bundle: flagged }));
  assert.equal(flaggedResponse.status, 202);
  const flags = JSON.parse(env.DB.submissions[1].flags);
  assert.ok(flags.includes("subprocess module"));
});

test("oversize PNG icon dimensions are rejected", async () => {
  const env = makeEnv();
  const response = await submit(env, submitForm({ icon: makePng(2048, 64) }));
  assert.equal(response.status, 400);
  assert.match((await response.json()).error, /dimensions/i);
});

test("approving a version downgrade is rejected", async () => {
  const env = makeEnv();
  const admin = { authorization: "Bearer admin-test-secret" };
  const first = await submit(env, submitForm({
    manifest: { id: "up", name: "Up", version: 2 } }));
  assert.equal(first.status, 202);
  const { id: firstId } = await first.json();
  const approveFirst = await call(`/api/admin/submissions/${firstId}/approve`, env,
    { method: "POST", headers: admin });
  assert.equal(approveFirst.status, 200);
  const second = await submit(env, submitForm({
    manifest: { id: "up", name: "Up", version: 1 } }));
  assert.equal(second.status, 202);
  const { id: secondId } = await second.json();
  const approveSecond = await call(`/api/admin/submissions/${secondId}/approve`, env,
    { method: "POST", headers: admin });
  assert.equal(approveSecond.status, 400);
  assert.match((await approveSecond.json()).error, /not newer/i);
  const third = await submit(env, submitForm({
    manifest: { id: "up", name: "Up", version: 3 } }));
  const { id: thirdId } = await third.json();
  const approveThird = await call(`/api/admin/submissions/${thirdId}/approve`, env,
    { method: "POST", headers: admin });
  assert.equal(approveThird.status, 200);
});
