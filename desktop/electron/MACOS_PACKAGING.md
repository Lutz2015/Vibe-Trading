# macOS arm64 packaging

This layer adds a Mac Apple Silicon desktop artifact on top of the same
Electron lifecycle shell used for Windows. It produces an **unsigned**
`dmg` + `zip` under `desktop/electron/release/` for review and local use.

## What is included

- Electron arm64 host (`electron` npm package; `prepare:electron` is platform-aware).
- A checksum-pinned CPython 3.12.14 runtime from
  [astral-sh/python-build-standalone](https://github.com/astral-sh/python-build-standalone)
  (`install_only_stripped`, `aarch64-apple-darwin`).
- `agent/requirements.txt` dependencies installed into that runtime's
  `lib/python3.12/site-packages`.
- The checked-out Person-Trading package with `--no-deps`.
- Production `frontend/dist` next to site-packages
  (`lib/python3.12/frontend/dist`), matching `api_server`'s relative lookup.
- Licenses copied beside the runtime.

## Deliberate exclusions

- No updater / release feed.
- No optional IM channel extras.
- No Developer ID signing or notarization (unsigned review artifact only).
- WeasyPrint PDF is optional at runtime: the packaging smoke test attempts a
  PDF write only if Pango/Cairo are visible via `DYLD_FALLBACK_LIBRARY_PATH`
  (Homebrew `/opt/homebrew/lib` or `/usr/local/lib`). End-user machines without
  those libraries still run the app; only PDF export may fail.

## Build (on an Apple Silicon Mac)

From a complete checkout:

```bash
cd frontend
npm ci
npm run build

cd ../desktop/electron
npm ci
npm run build
node scripts/test-backend-resolution.mjs
npm run runtime:mac          # long: downloads CPython + pip installs deps
npm run icon:mac
npm run installer:mac:review # unsigned dmg + zip + SHA256SUMS-mac-arm64.txt
```

Artifacts land in `desktop/electron/release/`.

## Opening the unsigned app

Gatekeeper will block a downloaded unsigned `.app`. On a machine you control:

```bash
xattr -dr com.apple.quarantine "/Applications/Person-Trading Desktop (Unofficial Community Build).app"
```

Or right-click → Open on first launch. **Do not publish unsigned review
artifacts.** A publishable build requires Developer ID Application signing and
notarization (not wired in this layer).

## Backend resolution (macOS)

`resolveBackend` looks for, in order:

1. `VIBE_TRADING_EXECUTABLE`
2. Packaged `Resources/backend/bin/python3` (launched with
   `import api_server; serve_main()`)
3. Source checkout `.venv/bin/person-trading` when a marked `pyproject.toml`
   (`name = "person-trading-ai"`) is an ancestor
4. `person-trading` on `PATH`

## Regenerating the Python pin

Update the constants in `scripts/build-backend.mjs`
(`PYTHON_VERSION`, `PBS_RELEASE`, `ARCHIVE_SHA256`) to a newer
python-build-standalone `install_only_stripped` aarch64-apple-darwin release
and re-run `npm run runtime:mac`.
