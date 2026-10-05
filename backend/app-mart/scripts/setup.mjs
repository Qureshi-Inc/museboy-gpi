#!/usr/bin/env node
import { randomBytes } from "node:crypto";
import { readdir } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { chmod, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { zipSync } from "fflate";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const tomlPath = path.join(root, "wrangler.toml");
const credentialPath = path.join(root, ".app-mart-credentials.json");
const deviceConfigPath = path.join(root, "device-config.json");
const args = process.argv.slice(2);
const deviceIndex = args.indexOf("--device");
const device = deviceIndex >= 0 ? args[deviceIndex + 1] : "";

function run(command, argv, { input, capture = false } = {}) {
  const result = spawnSync(command, argv, {
    cwd: root,
    input,
    encoding: "utf8",
    stdio: capture ? ["pipe", "pipe", "pipe"]
      : input !== undefined ? ["pipe", "inherit", "inherit"] : "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${command} ${argv.join(" ")} failed${result.stderr ? `: ${result.stderr.trim()}` : ""}`);
  }
  return result.stdout || "";
}

function parseJsonOutput(output, label) {
  const start = output.indexOf("[");
  const objectStart = output.indexOf("{");
  const index = start < 0 ? objectStart : objectStart < 0 ? start : Math.min(start, objectStart);
  if (index < 0) throw new Error(`Could not read ${label} output from Wrangler`);
  const parsed = JSON.parse(output.slice(index));
  return Array.isArray(parsed) ? parsed : parsed.databases || parsed.result || parsed;
}

function randomToken() { return randomBytes(32).toString("base64url"); }

async function ensureResources() {
  const d1Rows = parseJsonOutput(run("npx", ["wrangler", "d1", "list", "--json"], { capture: true }), "D1 list");
  let db = d1Rows.find(row => row.name === "museboy-app-mart");
  if (!db) {
    run("npx", ["wrangler", "d1", "create", "museboy-app-mart", "--location", "wnam", "--update-config", "--binding", "DB"]);
    const refreshed = parseJsonOutput(run("npx", ["wrangler", "d1", "list", "--json"], { capture: true }), "D1 list");
    db = refreshed.find(row => row.name === "museboy-app-mart");
  }
  const dbId = db.uuid || db.id;
  if (!dbId) throw new Error("Wrangler did not return a D1 database ID");
  let toml = await readFile(tomlPath, "utf8");
  const dbBlock = /\[\[d1_databases\]\][\s\S]*?(?=\n\[|\n$|$)/;
  if (dbBlock.test(toml)) {
    toml = toml.replace(dbBlock, `[[d1_databases]]\nbinding = "DB"\ndatabase_name = "museboy-app-mart"\ndatabase_id = "${dbId}"\nmigrations_dir = "migrations"`);
  } else {
    const stanza = `[[d1_databases]]\nbinding = "DB"\ndatabase_name = "museboy-app-mart"\ndatabase_id = "${dbId}"\nmigrations_dir = "migrations"\n`;
    toml = toml.replace("# setup.mjs inserts the account-specific D1 binding here.", stanza);
  }
  await writeFile(tomlPath, toml);

  let bucketRows;
  try {
    bucketRows = run("npx", ["wrangler", "r2", "bucket", "list"], { capture: true });
  } catch (error) {
    if (/Please enable R2 through the Cloudflare Dashboard/.test(error.message)) {
      throw new Error("This Cloudflare account has not activated R2. The marketplace host must open Cloudflare Dashboard → Storage & databases → R2 → Overview and complete Cloudflare's R2 activation/checkout. Then rerun `npm run setup`. No R2 bucket or Worker has been deployed yet.");
    }
    throw error;
  }
  if (!bucketRows.includes("museboy-app-mart")) {
    run("npx", ["wrangler", "r2", "bucket", "create", "museboy-app-mart", "--location", "wnam"]);
  }
  return dbId;
}

async function putSecret(name, value) {
  run("npx", ["wrangler", "secret", "put", name], { input: `${value}\n` });
}

async function collectFiles(directory, relative = "") {
  const files = {};
  for (const entry of await readdir(path.join(directory, relative), { withFileTypes: true })) {
    if (entry.name === "__pycache__" || entry.name.endsWith(".pyc")) continue;
    const child = path.posix.join(relative.replaceAll(path.sep, "/"), entry.name);
    if (entry.isSymbolicLink()) throw new Error(`Demo package contains a symlink: ${child}`);
    if (entry.isDirectory()) Object.assign(files, await collectFiles(directory, child));
    else if (entry.isFile()) files[child] = new Uint8Array(await readFile(path.join(directory, child)));
  }
  return files;
}

async function queueDemo(url, credentials) {
  const published = await fetch(`${url}/api/apps?q=Hello`).then(response => response.json());
  const pending = await fetch(`${url}/api/admin/submissions`, {
    headers: { authorization: `Bearer ${credentials.adminToken}` },
  }).then(response => response.ok ? response.json() : []);
  if (published.some(app => app.id === "hello") || pending.some(app => app.app_id === "hello")) {
    console.log("The Hello demo is already published or awaiting review.");
    return;
  }

  const demoDir = path.resolve(root, "../../apps/appmart/demo/hello");
  const manifest = JSON.parse(await readFile(path.join(demoDir, "app.json"), "utf8"));
  manifest.exec = "run.sh";
  const packageFiles = await collectFiles(demoDir);
  packageFiles["app.json"] = new TextEncoder().encode(JSON.stringify(manifest));
  const bundle = zipSync(packageFiles, { level: 6 });
  const body = new FormData();
  body.append("manifest", JSON.stringify(manifest));
  body.append("bundle", new Blob([bundle], { type: "application/zip" }), "hello.zip");
  body.append("icon", new Blob([packageFiles["icon.png"]], { type: "image/png" }), "icon.png");
  const response = await fetch(`${url}/api/submissions`, {
    method: "POST",
    headers: { authorization: `Bearer ${credentials.submitToken}` },
    body,
  });
  if (!response.ok) throw new Error(`Demo submission returned HTTP ${response.status}`);
  console.log("Queued the Hello demo app for review; approve it in the dashboard before downloading it on the GPi.");
}

async function main() {
  console.log("Checking Cloudflare sign-in…");
  try { run("npx", ["wrangler", "whoami"]); }
  catch {
    console.log("Opening Cloudflare’s browser sign-in…");
    run("npx", ["wrangler", "login"]);
    run("npx", ["wrangler", "whoami"]);
  }
  console.log("Creating/reusing App Mart storage…");
  await ensureResources();
  console.log("Applying database schema…");
  run("npx", ["wrangler", "d1", "migrations", "apply", "museboy-app-mart", "--remote"]);

  let credentials;
  try { credentials = JSON.parse(await readFile(credentialPath, "utf8")); }
  catch { credentials = { adminToken: randomToken(), submitToken: randomToken() }; }
  console.log("Saving private admin and upload credentials in this checkout…");
  await writeFile(credentialPath, JSON.stringify(credentials, null, 2) + "\n", { mode: 0o600 });
  await chmod(credentialPath, 0o600);
  await putSecret("ADMIN_TOKEN", credentials.adminToken);
  await putSecret("SUBMIT_TOKEN", credentials.submitToken);

  console.log("Deploying public catalog and private review API…");
  const deploy = run("npx", ["wrangler", "deploy"], { capture: true });
  process.stdout.write(deploy);
  const url = deploy.match(/https:\/\/[a-z0-9-]+\.[a-z0-9-]+\.workers\.dev/i)?.[0];
  if (!url) throw new Error("Deployment completed, but Wrangler’s workers.dev URL could not be detected. Copy the workers.dev URL from the output above into device-config.json as api_url.");

  const config = { api_url: url, submit_token: credentials.submitToken };
  await writeFile(deviceConfigPath, JSON.stringify(config, null, 2) + "\n", { mode: 0o600 });
  await chmod(deviceConfigPath, 0o600);
  try { await queueDemo(url, credentials); }
  catch (error) { console.warn(`\nThe backend is live, but the demo app could not be queued: ${error.message}`); }
  console.log(`\nApp Mart is live: ${url}`);
  console.log(`Review dashboard: ${url}/`);
  console.log("Admin token: saved locally in .app-mart-credentials.json; never publish or send it.");
  console.log("\nStep 3 — connect the GPi from this computer:");
  if (device) {
    run("node", ["scripts/configure-device.mjs", device]);
  } else {
    console.log("  npm run configure-device -- tendo@<gpi-ip-or-hostname>");
    console.log("This uses SSH/SCP and installs the private device config at /etc/gpi/appmart.json.");
  }
}

main().catch(error => {
  console.error(`\nApp Mart setup stopped: ${error.message}`);
  process.exitCode = 1;
});
