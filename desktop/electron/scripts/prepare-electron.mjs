import { createHash, randomUUID } from "node:crypto";
import { createReadStream } from "node:fs";
import {
  access,
  copyFile,
  mkdir,
  readFile,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import extractZip from "@electron-internal/extract-zip";
import { downloadArtifact } from "@electron/get";

const DOWNLOAD_ATTEMPTS = 4;
const DOWNLOAD_TIMEOUT_MS = 180_000;

function resolveElectronTarget({ platform = process.platform, arch = process.arch } = {}) {
  const electronPlatform = platform === "darwin" ? "darwin" : platform === "linux" ? "linux" : "win32";
  const electronArch = arch === "arm64" ? "arm64" : arch === "ia32" ? "ia32" : "x64";
  const executable =
    electronPlatform === "win32"
      ? "electron.exe"
      : electronPlatform === "darwin"
        ? "Electron.app/Contents/MacOS/Electron"
        : "electron";
  return {
    platform: electronPlatform,
    arch: electronArch,
    executable,
    archiveName: `electron-v{version}-${electronPlatform}-${electronArch}.zip`,
  };
}

export async function prepareElectron() {
  const electronRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const electronModule = path.join(electronRoot, "node_modules", "electron");
  const electronPackage = JSON.parse(
    await readFile(path.join(electronModule, "package.json"), "utf8"),
  );
  const checksums = JSON.parse(
    await readFile(path.join(electronModule, "checksums.json"), "utf8"),
  );
  const version = electronPackage.version;
  const targetSpec = resolveElectronTarget();
  const archiveName = targetSpec.archiveName.replace("{version}", version);
  const expected = checksums[archiveName];
  if (!expected) throw new Error(`Official checksum is missing for ${archiveName}`);

  const cacheRoot = path.join(electronRoot, ".cache");
  const targetDirectory = path.join(cacheRoot, "electron-dist");
  const target = path.join(targetDirectory, archiveName);
  await mkdir(targetDirectory, { recursive: true });
  let currentAttempt = 0;
  let lastReportedPercent = -10;

  await acquireVerifiedArchive({
    target,
    expected,
    attempts: DOWNLOAD_ATTEMPTS,
    timeoutMs: DOWNLOAD_TIMEOUT_MS,
    download: ({ signal }) =>
      downloadArtifact({
        version,
        artifactName: "electron",
        platform: targetSpec.platform,
        arch: targetSpec.arch,
        checksums,
        cacheRoot: path.join(cacheRoot, "electron-get"),
        downloadOptions: {
          signal,
          getProgressCallback: async ({ percent }) => {
            const currentPercent = Math.min(100, Math.floor(percent * 10) * 10);
            if (currentPercent >= lastReportedPercent + 10) {
              lastReportedPercent = currentPercent;
              console.log(
                `Electron download attempt ${currentAttempt}/${DOWNLOAD_ATTEMPTS}: ` +
                  `${currentPercent}%`,
              );
            }
          },
        },
      }),
    onAttempt: (attempt) => {
      currentAttempt = attempt;
      lastReportedPercent = -10;
    },
  });

  await installElectronRuntime({
    electronModule,
    archive: target,
    version,
    executable: targetSpec.executable,
    platform: targetSpec.platform,
  });
  console.log(`Electron archive and source runtime ready: ${target}`);
}

export async function acquireVerifiedArchive({
  target,
  expected,
  download,
  attempts = DOWNLOAD_ATTEMPTS,
  timeoutMs = DOWNLOAD_TIMEOUT_MS,
  sleep = (delayMs) => new Promise((resolve) => setTimeout(resolve, delayMs)),
  onAttempt = () => {},
}) {
  if (await sha256(target) === expected) return;

  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    onAttempt(attempt);
    try {
      const downloaded = await download({ signal: AbortSignal.timeout(timeoutMs), attempt });
      const actual = await sha256(downloaded);
      if (actual !== expected) {
        throw new Error(`checksum mismatch: expected ${expected}, got ${actual}`);
      }
      await replaceVerifiedFile(downloaded, target, expected);
      return;
    } catch (error) {
      lastError = error;
      if (attempt < attempts) {
        const delayMs = attempt * 2_000;
        console.warn(
          `Electron download attempt ${attempt}/${attempts} failed: ${formatError(error)}; ` +
            `retrying in ${delayMs / 1_000}s`,
        );
        await sleep(delayMs);
      }
    }
  }
  throw new Error(
    `Electron download failed after ${attempts} bounded attempts: ${formatError(lastError)}`,
    { cause: lastError },
  );
}

export async function installElectronRuntime({
  electronModule,
  archive,
  version,
  executable = "electron.exe",
  platform = "win32",
}) {
  const dist = path.join(electronModule, "dist");
  const executablePath = path.join(dist, executable);
  const versionFile = path.join(dist, "version");
  const pathFile = path.join(electronModule, "path.txt");
  if (
    (await readTrimmed(versionFile))?.replace(/^v/u, "") === version &&
    (await readTrimmed(pathFile)) === executable &&
    (await exists(executablePath))
  ) {
    return;
  }

  const temporaryDist = `${dist}.tmp-${process.pid}-${randomUUID()}`;
  const temporaryPathFile = `${pathFile}.tmp-${process.pid}-${randomUUID()}`;
  try {
    await rm(temporaryDist, { recursive: true, force: true });
    await mkdir(temporaryDist, { recursive: true });
    await extractZip(archive, { dir: temporaryDist });

    const extractedExecutable = path.join(temporaryDist, executable);
    const extractedVersion = (await readTrimmed(path.join(temporaryDist, "version")))?.replace(
      /^v/u,
      "",
    );
    if (!(await exists(extractedExecutable)) || extractedVersion !== version) {
      throw new Error(
        `Electron archive did not contain the expected ${platform} ${version} runtime`,
      );
    }

    const extractedTypes = path.join(temporaryDist, "electron.d.ts");
    if (await exists(extractedTypes)) {
      await copyFile(extractedTypes, path.join(electronModule, "electron.d.ts"));
      await rm(extractedTypes, { force: true });
    }

    await rm(dist, { recursive: true, force: true });
    await rename(temporaryDist, dist);
    await writeFile(temporaryPathFile, executable, "utf8");
    await rm(pathFile, { force: true });
    await rename(temporaryPathFile, pathFile);
  } finally {
    await rm(temporaryDist, { recursive: true, force: true });
    await rm(temporaryPathFile, { force: true });
  }
}

async function replaceVerifiedFile(source, target, expected) {
  const temporaryTarget = `${target}.tmp-${process.pid}-${randomUUID()}`;
  try {
    await copyFile(source, temporaryTarget);
    const copied = await sha256(temporaryTarget);
    if (copied !== expected) {
      throw new Error(`copied archive checksum mismatch: expected ${expected}, got ${copied}`);
    }
    await rm(target, { force: true });
    await rename(temporaryTarget, target);
  } finally {
    await rm(temporaryTarget, { force: true });
  }
}

async function sha256(file) {
  try {
    await access(file);
  } catch (error) {
    if (error && error.code === "ENOENT") return undefined;
    throw error;
  }
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest("hex");
}

async function readTrimmed(file) {
  try {
    return (await readFile(file, "utf8")).trim();
  } catch (error) {
    if (error && error.code === "ENOENT") return undefined;
    throw error;
  }
}

async function exists(file) {
  try {
    await access(file);
    return true;
  } catch (error) {
    if (error && error.code === "ENOENT") return false;
    throw error;
  }
}

function formatError(error) {
  return error instanceof Error ? error.message : String(error);
}

const invokedAsScript = process.argv[1]
  ? pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url
  : false;
if (invokedAsScript) await prepareElectron();
