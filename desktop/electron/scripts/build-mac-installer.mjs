#!/usr/bin/env node
/**
 * Build an unsigned macOS arm64 review DMG + ZIP under release/.
 * Never publish these artifacts: Gatekeeper will treat them as untrusted
 * unless a Developer ID identity + notarization is supplied separately.
 */
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const electronRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const unsignedEnvironment = { ...process.env };
for (const name of [
  "CSC_LINK",
  "CSC_KEY_PASSWORD",
  "WIN_CSC_LINK",
  "WIN_CSC_KEY_PASSWORD",
  "AZURE_TENANT_ID",
  "AZURE_CLIENT_ID",
  "AZURE_CLIENT_SECRET",
  "APPLE_ID",
  "APPLE_APP_SPECIFIC_PASSWORD",
  "APPLE_TEAM_ID",
]) {
  delete unsignedEnvironment[name];
}
unsignedEnvironment.CSC_IDENTITY_AUTO_DISCOVERY = "false";
unsignedEnvironment.ELECTRON_BUILDER_COMPRESSION_LEVEL = "7";

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: electronRoot,
    stdio: "inherit",
    ...options,
  });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} failed with status ${String(result.status)}`);
  }
}

async function sha256(file) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest("hex");
}

run(process.execPath, [
  path.join(electronRoot, "node_modules", "electron-builder", "cli.js"),
  "--publish",
  "never",
  "--mac",
  "dmg",
  "zip",
  "--arm64",
], { env: unsignedEnvironment });

const releaseDirectory = path.join(electronRoot, "release");
const artifacts = (await readdir(releaseDirectory, { withFileTypes: true }))
  .filter(
    (entry) =>
      entry.isFile() &&
      /Person-Trading-Desktop-Unofficial-.*-arm64\.(dmg|zip)$/u.test(entry.name),
  )
  .map((entry) => path.join(releaseDirectory, entry.name));

if (artifacts.length < 1) {
  throw new Error(`Expected at least one arm64 dmg/zip in ${releaseDirectory}`);
}

const checksumLines = [];
for (const artifact of artifacts) {
  const digest = await sha256(artifact);
  checksumLines.push(`${digest}  ${path.basename(artifact)}`);
  console.log(`Unsigned review artifact: ${artifact}`);
  console.log(`SHA-256: ${digest}`);
}
await writeFile(
  path.join(releaseDirectory, "SHA256SUMS-mac-arm64.txt"),
  `${checksumLines.join("\n")}\n`,
  "ascii",
);
console.log("Unsigned macOS arm64 review artifacts ready; do not publish.");
