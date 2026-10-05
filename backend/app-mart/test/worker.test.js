import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";

class MemoryDB {
  constructor() { this.apps = []; this.submissions = []; }
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
            return { results: db.submissions.filter(item => item.status === "pending") };
          }
          const [category, , query] = values;
          return { results: db.apps.filter(app =>
            (!category || app.category === category) &&
            (!query || `${app.name} ${app.description} ${app.author}`.toLowerCase()
              .includes(query.toLowerCase()))) };
        },
        async first() {
          if (sql.includes("FROM submissions")) {
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
              icon_key, bundle_key, sha256, submitted_at] = values;
            db.submissions.push({ id, app_id, name, description, category, author,
              version, icon_key, bundle_key, sha256, submitted_at, status: "pending" });
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
  assert.deepEqual((await apps.json()).map(app => app.id), ["blocks"]);
  const categories = await call("/api/categories", env);
  assert.deepEqual((await categories.json()).map(row => row.category).sort(), ["demo", "games"]);
});

test("submission stays private until an admin approves it, then is downloadable", async () => {
  const env = makeEnv();
  const form = new FormData();
  form.set("manifest", JSON.stringify({ id: "hello", name: "Hello", description: "A demo",
    category: "demo", author: "Moiz", version: 1 }));
  form.set("bundle", new File([new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0x00])], "hello.zip"));
  form.set("icon", new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])], "icon.png"));

  const unauthorized = await call("/api/submissions", env, { method: "POST", body: form });
  assert.equal(unauthorized.status, 401);
  const response = await call("/api/submissions", env, { method: "POST", body: form,
    headers: { authorization: "Bearer submit-test-secret" } });
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
