# p1-deps-prototype

Minimal working prototype of the M4 redesign from my P1 "AI Product Teardown" of Lovable
(course 34621 Web Systems, Universidad San Jorge). Side quest: *Prototype it*.

- **Live demo:** https://magical-platypus-bfc42e.netlify.app/
- **Evidence:** [`evidence/`](evidence/) (8 screenshots + [`measurements.txt`](evidence/measurements.txt))

## What it demonstrates

The same tiny app (preact + htm counter) is built from two lock files:

| Lock | Packages |
|---|---|
| `lock.v1.json` | preact 11.0.2 (`preact`, `preact/hooks`), htm 3.1.1 |
| `lock.v2.json` | v1 + @preact/signals-core 1.14.4 (one package added) |

Each version is served in two modes that differ **only** in how dependency URLs are named:

| Mode | Dependency URL | When one package is added |
|---|---|---|
| **Baseline** (global `?v=`, like Vite's dev server) | `/deps-global/preact.js?v=<hash of the whole lock>` | every URL changes, so every file is downloaded again |
| **Redesign** (content-addressed) | `/deps/<first 16 hex of sha256(bytes)>.js`, listed in an import map | only the new file has a new URL; the rest stay in the browser cache |

Both modes use an import map and the same caching headers (`public, max-age=31536000, immutable`);
the HTML pages are `no-store`. Each page prints a table from the Resource Timing API
(`transferSize` 0 = no network transfer).

## Mapping to my M4 finding (report, Figure 4)

My HAR of a 22-minute Lovable session showed that the preview (a Vite dev server) sent 124 dependency
requests (14,287 kB) with `Cache-Control: no-store`, and that 51 unchanged files were re-fetched because a single
global `?v=` hash renames every dependency URL whenever any package changes. The redesign in Figure 4
names each file by the hash of its bytes, serves it `immutable`, and keeps an import map in the
(no-store) HTML, so a package change touches one import-map entry.

The two modes here reproduce those two schemes on a small scale.

## Result (Chrome 155.0.8059.85, macOS 27.0.1, Ethernet, 25 Oct 2026, one run per mode)

Going from v1 to v2 (dependency `transferSize`, from the on-page tables):

| | v2 total | Unchanged files |
|---|---|---|
| Baseline | 10,203 B, 4 of 4 from the network | re-downloaded (7,970 B) |
| Redesign | 2,233 B, 1 of 4 from the network | 0 B, served from the browser cache |

The redesign transferred 78.1 % fewer dependency bytes for this change (78.5 % counting body bytes only;
Chrome adds a fixed 300 B header allowance to every `transferSize`). Full details and caveats:
[`evidence/measurements.txt`](evidence/measurements.txt).

## Repository layout

```
resolver.py      builds site/ from the lock files (Python 3.12+, standard library only)
lock.v1.json     pinned versions + sha512 integrity (v1)
lock.v2.json     same + @preact/signals-core (v2)
netlify.toml     publish directory = site
site/_headers    per-path Cache-Control and related headers
site/            generated output (committed, deployed as-is, no build step)
evidence/        screenshots and measurement log
```

## How the resolver works

For each import in a lock file:

1. Validates the package name and exact version (allowlist regexes).
2. Fetches `registry.npmjs.org/<name>/<version>` and checks that its `dist.integrity` equals the lock's.
3. Downloads the tarball (registry host only, 10 MB cap) and verifies its sha512 against the lock (SRI format)
   **before** opening it.
4. Reads exactly one file from the tarball into memory: no package scripts are run, nothing is extracted to disk,
   links are rejected, and output names come from hashes, never from archive paths.
5. Writes the file to `site/deps/<sha256[:16]>.js` and `site/deps-global/<name>.js`, then generates the four pages
   and a landing page.

The build is reproducible: the same lock files always produce the same `/deps/` filenames.

## Reproduce

```bash
python3.12 resolver.py lock.v1.json lock.v2.json     # rebuild site/
shasum -a 256 site/deps/*.js                         # first 16 hex = filename
python3.12 -m http.server -d site 8000               # smoke test only: no caching headers locally
```

Deploy `site/` to a host that reads `_headers` (Netlify or Cloudflare Pages; not GitHub Pages), then check
the headers:

```bash
curl -sI https://magical-platypus-bfc42e.netlify.app/deps/4d2617f3f0825502.js | grep -i cache-control
```

To measure: open a fresh Incognito window and DevTools (Network tab, "Disable cache" off, "Keep log" on),
load `/baseline-v1/`, click the v2 link, and compare with `/redesign-v1/` then v2 in a new Incognito window.

## Limitations

- A static demo of the naming scheme, not Lovable's system: no agent, no Vite, no HMR, no edge CDN behaviour
  beyond what Netlify does by default.
- A tiny sample: 4 files (22,500 B uncompressed) and one run per mode. The M4 figures (124 requests, 14,287 kB)
  are a different scale, and this prototype does not validate the 86.8 % projection; it shows the mechanism.
- Resource Timing reports 0 for both memory-cache and disk-cache hits, so the claim is "no network transfer".
  `transferSize` includes a fixed 300 B header allowance in Chrome.
- Netlify injects a "Powered by Netlify" badge and a script into the HTML, so the served HTML is not
  byte-identical to `site/`. Dependency files are not affected.
- No Content-Security-Policy: the inline import map and module script would need hashes or `'unsafe-inline'`.
- Content addresses are truncated to 64 bits, which is enough for a demo; production should use the full sha256.

## Security notes

- Tarballs are verified against the pinned sha512 before parsing; a single changed character in the lock makes the
  resolver stop with an integrity error.
- The site sends no cookies, loads no third-party code of its own, and builds every table cell with `textContent`
  (never `innerHTML`).
- No tokens or personal data in the repo; commits use a GitHub noreply address.

## Third-party code

`site/deps*/` contains unmodified files from preact 11.0.2 (MIT), @preact/signals-core 1.14.4 (MIT) and
htm 3.1.1 (Apache-2.0), fetched from the npm registry and verified against the integrity values in the lock files.

## AI assistance

Built with help from an AI assistant (Claude), which explained, checked and drafted code and text; all measurements
come from my own browser. Declared in Appendix C of the report.
