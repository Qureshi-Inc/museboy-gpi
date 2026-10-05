#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { access, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const host = process.argv[2];
const config = path.join(root, "device-config.json");

if (!host || host.startsWith("-") || !/^[a-zA-Z0-9_.@:-]+$/.test(host)) {
  console.error("Usage: npm run configure-device -- user@gpi-host");
  process.exit(2);
}
try { await access(config); }
catch {
  console.error("device-config.json is missing. Run npm run setup first.");
  process.exit(2);
}
const parsed = JSON.parse(await readFile(config, "utf8"));
if (!/^https:\/\//.test(parsed.api_url) || typeof parsed.submit_token !== "string") {
  throw new Error("device-config.json has invalid settings");
}

function run(command, args) {
  const result = spawnSync(command, args, { cwd: root, stdio: "inherit" });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} exited with status ${result.status}`);
}

try {
  console.log(`Copying private App Mart config to ${host}…`);
  run("scp", ["-o", "StrictHostKeyChecking=accept-new", config, `${host}:/tmp/museboy-appmart.json`]);
  console.log("Installing config with root-only write access…");
  run("ssh", ["-t", host,
    "sudo install -D -o root -g tendo -m 0640 /tmp/museboy-appmart.json /etc/gpi/appmart.json && rm -f /tmp/museboy-appmart.json"]);
  console.log("Done. Restart App Mart on the GPi to load the live catalog and Share action.");
} catch (error) {
  console.error(`Could not configure GPi: ${error.message}`);
  process.exitCode = 1;
}
