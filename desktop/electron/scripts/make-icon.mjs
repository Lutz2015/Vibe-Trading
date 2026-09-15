#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import { mkdir, rm, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const electronRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourceIcon = path.resolve(electronRoot, "..", "..", "assets", "icon.png");
const iconsetDir = path.join(electronRoot, "build", "AppIcon.iconset");
const outputIcns = path.join(electronRoot, "build", "icon.icns");

const sizes = [16, 32, 64, 128, 256, 512, 1024];

async function main() {
  if (process.platform !== "darwin") {
    throw new Error("icon generation requires macOS iconutil");
  }
  await stat(sourceIcon);
  await rm(iconsetDir, { recursive: true, force: true });
  await mkdir(iconsetDir, { recursive: true });
  await mkdir(path.dirname(outputIcns), { recursive: true });

  for (const size of sizes) {
    for (const scale of size === 1024 ? [1] : [1, 2]) {
      const pixel = size * scale;
      if (pixel > 1024) continue;
      const name =
        scale === 2
          ? `icon_${size}x${size}@2x.png`
          : `icon_${size}x${size}.png`;
      execFileSync("sips", [
        "-z",
        String(pixel),
        String(pixel),
        sourceIcon,
        "--out",
        path.join(iconsetDir, name),
      ], { stdio: "inherit" });
    }
  }

  await rm(outputIcns, { force: true });
  execFileSync("iconutil", ["-c", "icns", iconsetDir, "-o", outputIcns], {
    stdio: "inherit",
  });
  console.log(`Icon ready: ${outputIcns}`);
}

await main();
