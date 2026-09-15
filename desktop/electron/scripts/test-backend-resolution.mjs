import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { resolveBackend } from "../dist/backend-manager.js";

const isWindows = process.platform === "win32";
const cliName = isWindows ? "person-trading.exe" : "person-trading";
const venvScriptsDir = isWindows ? "Scripts" : "bin";
const packagedPython = isWindows
  ? "backend/python.exe"
  : "backend/bin/python3";

const testRoot = await mkdtemp(path.join(os.tmpdir(), "vibe-desktop-resolution-"));

try {
  const explicit = await makeExecutable(`explicit/${cliName}`);
  const packagedResources = path.join(testRoot, "packaged/resources");
  const packagedPythonPath = await makeExecutable(path.posix.join("packaged/resources", packagedPython));
  const pathExecutable = await makeExecutable(`path-bin/${cliName}`);

  assertResolution(
    resolveBackend(options({
      appPath: path.join(testRoot, "packaged/app.asar"),
      resourcesPath: packagedResources,
      executableOverride: explicit,
      pathEnvironment: path.dirname(pathExecutable),
    })),
    explicit,
    "explicit override must take precedence",
  );

  const packagedResolution = resolveBackend(options({
    appPath: path.join(testRoot, "packaged/app.asar"),
    resourcesPath: packagedResources,
    pathEnvironment: path.dirname(pathExecutable),
  }));
  assertResolution(packagedResolution, packagedPythonPath, "exact packaged resource must be selected");
  assert.equal(packagedResolution?.includeServeCommand, false);

  const sourceRoot = path.join(testRoot, "marked-source");
  await writeProjectMarker(sourceRoot, "person-trading-ai");
  const sourceExecutable = await makeExecutable(
    `marked-source/.venv/${venvScriptsDir}/${cliName}`,
  );
  const sourceResolution = resolveBackend(options({
    appPath: path.join(sourceRoot, "desktop/electron"),
    resourcesPath: path.join(testRoot, "empty-resources"),
    moduleDirectory: path.join(sourceRoot, "desktop/electron/dist"),
    pathEnvironment: path.dirname(pathExecutable),
  }));
  assertResolution(sourceResolution, sourceExecutable, "marked source virtual environment must be selected");

  const plantedRoot = path.join(testRoot, "unmarked-ancestor");
  const plantedPython = await makeExecutable(path.posix.join("unmarked-ancestor", packagedPython));
  const plantedAppPath = path.join(plantedRoot, "workspace/desktop/electron");
  const safePathExecutable = await makeExecutable(`safe-path/${cliName}`);
  const unmarkedResolution = resolveBackend(options({
    appPath: plantedAppPath,
    resourcesPath: path.join(plantedAppPath, "resources"),
    moduleDirectory: path.join(plantedAppPath, "dist"),
    pathEnvironment: path.dirname(safePathExecutable),
  }));
  assert.notEqual(path.resolve(unmarkedResolution?.executable ?? ""), path.resolve(plantedPython));
  assertResolution(
    unmarkedResolution,
    safePathExecutable,
    "unmarked ancestor executable must be ignored in favor of PATH",
  );

  const wrongProjectRoot = path.join(testRoot, "wrong-project");
  await writeProjectMarker(wrongProjectRoot, "another-project");
  await makeExecutable(`wrong-project/.venv/${venvScriptsDir}/${cliName}`);
  assertResolution(
    resolveBackend(options({
      appPath: path.join(wrongProjectRoot, "desktop/electron"),
      resourcesPath: path.join(testRoot, "wrong-project-resources"),
      moduleDirectory: path.join(wrongProjectRoot, "desktop/electron/dist"),
      pathEnvironment: path.dirname(pathExecutable),
    })),
    pathExecutable,
    "wrong project marker must not authorize a source virtual environment",
  );

  assertResolution(
    resolveBackend(options({
      appPath: path.join(sourceRoot, "desktop/electron"),
      resourcesPath: path.join(testRoot, "disabled-source-resources"),
      moduleDirectory: path.join(sourceRoot, "desktop/electron/dist"),
      allowSourceDiscovery: false,
      pathEnvironment: path.dirname(pathExecutable),
    })),
    pathExecutable,
    "packaged mode must not search source ancestors even when a marker exists",
  );

  console.log(JSON.stringify({
    platform: process.platform,
    explicitOverride: true,
    exactPackagedResource: true,
    markedSourceRoot: true,
    unmarkedAncestorRejected: true,
    wrongMarkerRejected: true,
    packagedSourceDiscoveryDisabled: true,
  }, null, 2));
} finally {
  await rm(testRoot, { recursive: true, force: true });
}

function options(overrides) {
  return {
    appPath: path.join(testRoot, "empty-app"),
    resourcesPath: path.join(testRoot, "empty-resources"),
    moduleDirectory: path.join(testRoot, "empty-module"),
    allowSourceDiscovery: true,
    pathEnvironment: "",
    ...overrides,
  };
}

async function makeExecutable(relativePath) {
  const target = path.join(testRoot, ...relativePath.split("/"));
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, "test fixture", "utf8");
  return target;
}

async function writeProjectMarker(root, projectName) {
  await mkdir(root, { recursive: true });
  await writeFile(
    path.join(root, "pyproject.toml"),
    `[project]\nname = "${projectName}"\nversion = "0.0.0"\n`,
    "utf8",
  );
}

function assertResolution(resolution, expectedExecutable, message) {
  assert.ok(resolution, message);
  assert.equal(path.resolve(resolution.executable), path.resolve(expectedExecutable), message);
}
