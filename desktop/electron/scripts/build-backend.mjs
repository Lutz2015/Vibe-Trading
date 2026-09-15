#!/usr/bin/env node
/**
 * Assemble a relocatable macOS ARM backend runtime under runtime/backend.
 * Mirrors the Windows build-backend.ps1 contract: checksum-pinned CPython,
 * project install, frontend dist, smoke tests.
 */
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { access, cp, mkdir, mkdtemp, readdir, readFile, rename, rm, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const electronRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(electronRoot, "..", "..");
const runtimeRoot = path.join(electronRoot, "runtime", "backend");
const cacheRoot = path.join(electronRoot, ".cache", "python");

// python-build-standalone (astral-sh), CPython 3.12.14 for Apple Silicon.
const PYTHON_VERSION = "3.12.14";
const PBS_RELEASE = "20260901";
const ARCHIVE_NAME = `cpython-${PYTHON_VERSION}+${PBS_RELEASE}-aarch64-apple-darwin-install_only_stripped.tar.gz`;
const ARCHIVE_URL = `https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${ARCHIVE_NAME}`;
const ARCHIVE_SHA256 = "81a359f1cfadd4da11766534c5913791cea55f26e1bb902cacd2a531bb1e4b2b";

const clean = process.argv.includes("--clean");

function fail(message) {
  console.error(message);
  process.exit(1);
}

function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      stdio: ["inherit", "pipe", "pipe"],
      ...options,
    });
    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", (chunk) => {
      stdout += chunk;
      process.stdout.write(chunk);
    });
    child.stderr?.on("data", (chunk) => {
      stderr += chunk;
      process.stderr.write(chunk);
    });
    child.once("error", reject);
    child.once("exit", (code) => {
      if (code === 0) resolve({ stdout, stderr });
      else reject(new Error(`${command} ${args.join(" ")} exited with code ${code}`));
    });
  });
}

async function exists(target) {
  try {
    await access(target);
    return true;
  } catch {
    return false;
  }
}

async function sha256(file) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest("hex");
}

async function downloadVerified(url, destination, expectedSha256, label) {
  if (await exists(destination)) {
    const actual = await sha256(destination);
    if (actual === expectedSha256) return;
    console.warn(`${label} checksum mismatch in cache; re-downloading`);
  }
  await mkdir(path.dirname(destination), { recursive: true });
  const temporary = `${destination}.download-${process.pid}`;
  await run("curl", [
    "--fail",
    "--location",
    "--silent",
    "--show-error",
    "--connect-timeout",
    "30",
    "--max-time",
    "300",
    "--retry",
    "3",
    "--retry-delay",
    "2",
    "--retry-all-errors",
    "--output",
    temporary,
    url,
  ]);
  const actual = await sha256(temporary);
  if (actual !== expectedSha256) {
    await rm(temporary, { force: true });
    throw new Error(`${label} checksum mismatch. Expected ${expectedSha256}, got ${actual}`);
  }
  await rm(destination, { force: true });
  await rename(temporary, destination);
}

async function directorySize(root) {
  let total = 0;
  async function walk(current) {
    const entries = await readdir(current, { withFileTypes: true });
    for (const entry of entries) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) await walk(full);
      else {
        const info = await stat(full).catch(() => null);
        if (info) total += info.size;
      }
    }
  }
  await walk(root);
  return total;
}

function homebrewLibPaths() {
  const prefixes = [process.env.HOMEBREW_PREFIX, "/opt/homebrew", "/usr/local"].filter(Boolean);
  return [...new Set(prefixes.map((prefix) => path.join(prefix, "lib")))];
}

async function main() {
  if (process.platform !== "darwin" || process.arch !== "arm64") {
    fail("build-backend.mjs currently targets macOS arm64 only.");
  }

  const frontendDist = path.join(repoRoot, "frontend", "dist");
  if (!(await exists(path.join(frontendDist, "index.html")))) {
    fail("frontend/dist is missing. Build the production frontend before assembling the runtime.");
  }

  if (clean && (await exists(runtimeRoot))) {
    await rm(runtimeRoot, { recursive: true, force: true });
  }

  await mkdir(cacheRoot, { recursive: true });
  const archivePath = path.join(cacheRoot, ARCHIVE_NAME);
  await downloadVerified(ARCHIVE_URL, archivePath, ARCHIVE_SHA256, "Python standalone");

  const extractRoot = await mkdtemp(path.join(os.tmpdir(), "vibe-mac-python-"));
  try {
    await run("tar", ["-xzf", archivePath, "-C", extractRoot]);
    const extractedPython = path.join(extractRoot, "python");
    if (!(await exists(path.join(extractedPython, "bin", "python3")))) {
      throw new Error("python-build-standalone archive did not contain bin/python3");
    }
    await rm(runtimeRoot, { recursive: true, force: true });
    await mkdir(path.dirname(runtimeRoot), { recursive: true });
    await rename(extractedPython, runtimeRoot);
  } finally {
    await rm(extractRoot, { recursive: true, force: true });
  }

  const runtimePython = path.join(runtimeRoot, "bin", "python3");
  const versionProbe = await run(runtimePython, [
    "-c",
    "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
  ]);
  const pyShort = versionProbe.stdout.trim();
  if (!/^\d+\.\d+$/u.test(pyShort)) {
    throw new Error(`Could not determine packaged Python minor version: ${versionProbe.stdout}`);
  }
  const sitePackages = path.join(runtimeRoot, "lib", `python${pyShort}`, "site-packages");
  await mkdir(sitePackages, { recursive: true });

  const requirements = path.join(repoRoot, "agent", "requirements.txt");
  console.log("Installing base dependencies into the embedded runtime…");
  await run(
    runtimePython,
    [
      "-m",
      "pip",
      "install",
      "--disable-pip-version-check",
      "--upgrade",
      "--target",
      sitePackages,
      "--requirement",
      requirements,
    ],
    { cwd: repoRoot },
  );

  console.log("Installing Person-Trading (--no-deps)…");
  await run(
    runtimePython,
    [
      "-m",
      "pip",
      "install",
      "--disable-pip-version-check",
      "--no-deps",
      "--upgrade",
      "--target",
      sitePackages,
      repoRoot,
    ],
    { cwd: repoRoot },
  );

  // api_server resolves frontend as Path(__file__).parent.parent / "frontend" / "dist"
  // which, for a top-level module in site-packages, is lib/pythonX.Y/frontend/dist.
  const runtimeFrontendParent = path.join(runtimeRoot, "lib", `python${pyShort}`, "frontend");
  await rm(runtimeFrontendParent, { recursive: true, force: true });
  await mkdir(runtimeFrontendParent, { recursive: true });
  await cp(frontendDist, path.join(runtimeFrontendParent, "dist"), { recursive: true });

  await cp(path.join(repoRoot, "LICENSE"), path.join(runtimeRoot, "Person-Trading-LICENSE.txt"));
  await cp(path.join(repoRoot, "NOTICE"), path.join(runtimeRoot, "Person-Trading-NOTICE.txt"));

  // Prune packaged test suites (same policy as the Windows runtime).
  const testDirNames = new Set(["test", "tests"]);
  const pruneRoots = [];
  async function collectTests(dir) {
    const entries = await readdir(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const full = path.join(dir, entry.name);
      if (testDirNames.has(entry.name)) pruneRoots.push(full);
      else await collectTests(full);
    }
  }
  await collectTests(sitePackages);
  pruneRoots.sort((a, b) => a.length - b.length);
  let removed = 0;
  for (const dir of pruneRoots) {
    if (removed && pruneRoots.some((parent) => dir.startsWith(`${parent}${path.sep}`))) continue;
    await rm(dir, { recursive: true, force: true });
    removed += 1;
  }
  console.log(`Pruned ${removed} packaged test directories`);

  const libraryPath = homebrewLibPaths().join(":");
  const smokeEnvironment = {
    ...process.env,
    DYLD_FALLBACK_LIBRARY_PATH: libraryPath
      ? `${libraryPath}${process.env.DYLD_FALLBACK_LIBRARY_PATH ? `:${process.env.DYLD_FALLBACK_LIBRARY_PATH}` : ""}`
      : process.env.DYLD_FALLBACK_LIBRARY_PATH,
  };

  console.log("Running import smoke test…");
  const smokeScript = `
import importlib.util
import api_server
import cli

for optional_module in (
    "dingtalk_stream",
    "discord",
    "telegram",
    "neonize",
    "qrcode",
    "mt5",
):
    assert importlib.util.find_spec(optional_module) is None, optional_module

weasy_error = None
try:
    from weasyprint import HTML
    assert len(HTML(string="<p>PDF smoke</p>").write_pdf()) > 1000
    print("weasyprint-pdf: ok")
except Exception as exc:  # noqa: BLE001 - packaging smoke, not product path
    weasy_error = exc
    print(f"weasyprint-pdf: skipped ({exc.__class__.__name__}: {exc})")

print("embedded backend import checks OK")
`;
  await run(runtimePython, ["-c", smokeScript], { env: smokeEnvironment, cwd: runtimeRoot });

  const inventoryScript = `
import importlib.metadata
import json

packages = sorted(
    (
        {"name": dist.metadata["Name"], "version": dist.version}
        for dist in importlib.metadata.distributions()
        if dist.metadata["Name"]
    ),
    key=lambda item: item["name"].lower(),
)
print(json.dumps({"format": "python-importlib-metadata", "packages": packages}, indent=2))
`;
  const inventory = await run(runtimePython, ["-c", inventoryScript], {
    env: smokeEnvironment,
    cwd: runtimeRoot,
  });
  await writeFile(
    path.join(runtimeRoot, "python-dependency-inventory.json"),
    inventory.stdout,
    "utf8",
  );

  const size = await directorySize(runtimeRoot);
  console.log(
    `Backend runtime ready: ${runtimeRoot} (${(size / (1024 * 1024)).toFixed(1)} MB)`,
  );
}

await main();
