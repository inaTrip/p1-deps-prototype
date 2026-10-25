"""Build two dependency-URL schemes (global ?v= vs content-addressed) from lock files.

Usage: python3.12 resolver.py lock.v1.json lock.v2.json
"""
import base64
import hashlib
import io
import json
import re
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

REGISTRY = "https://registry.npmjs.org/"
SITE = Path(__file__).resolve().parent / "site"
MAX_BYTES = 10 * 1024 * 1024  # refuse anything larger than 10 MB
NAME_RE = re.compile(r"^(@[a-z0-9._-]+/)?[a-z0-9._-]+$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def fail(msg: str) -> None:
    sys.exit(f"ERROR: {msg}")


def fetch(url: str) -> bytes:
    if not url.startswith(REGISTRY):
        fail(f"refusing URL outside the npm registry: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "p1-deps-prototype"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        fail(f"response larger than {MAX_BYTES} bytes: {url}")
    return data


def verify_sri(data: bytes, sri: str, label: str) -> None:
    algo, sep, b64 = sri.partition("-")
    if algo != "sha512" or not sep:
        fail(f"{label}: expected a single sha512 SRI value, got {sri!r}")
    expected = base64.b64decode(b64, validate=True)
    actual = hashlib.sha512(data).digest()
    if actual != expected:
        fail(f"{label}: integrity mismatch, tarball rejected")


def download_package(name: str, version: str, integrity: str) -> bytes:
    if not NAME_RE.match(name) or not VERSION_RE.match(version):
        fail(f"invalid package name or version: {name}@{version}")
    meta = json.loads(fetch(f"{REGISTRY}{name}/{version}"))
    if meta["dist"]["integrity"] != integrity:
        fail(f"{name}@{version}: registry integrity differs from the lock file")
    tgz = fetch(meta["dist"]["tarball"])
    verify_sri(tgz, integrity, f"{name}@{version}")
    return tgz


def extract_one(tgz: bytes, rel_path: str) -> bytes:
    if rel_path.startswith("/") or ".." in rel_path.split("/"):
        fail(f"unsafe path in lock file: {rel_path}")
    with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as tar:
        try:
            member = tar.getmember("package/" + rel_path)
        except KeyError:
            fail(f"file not found in tarball: {rel_path}")
        if not member.isfile():
            fail(f"not a regular file (link or directory): {rel_path}")
        return tar.extractfile(member).read()


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%%MODE%% %%LABEL%%</title>
<script type="importmap">
%%IMPORTMAP%%
</script>
<style>
  body { font: 15px/1.4 system-ui, sans-serif; margin: 1.5rem; }
  table { border-collapse: collapse; margin-top: 1rem; }
  th, td { border: 1px solid #999; padding: .25rem .6rem; text-align: right; }
  th:first-child, td:first-child { text-align: left; font-family: ui-monospace, monospace; }
</style>
</head>
<body>
<h1>%%MODE%% / %%LABEL%%</h1>
<nav>baseline: <a href="/baseline-v1/">v1</a> <a href="/baseline-v2/">v2</a>
  | redesign: <a href="/redesign-v1/">v1</a> <a href="/redesign-v2/">v2</a></nav>
<div id="app"></div>
<p id="sig"></p>
<table id="rt"><thead><tr>
  <th>dependency URL</th><th>transferSize</th><th>encodedBodySize</th>
  <th>decodedBodySize</th><th>source</th><th>duration (ms)</th>
</tr></thead><tbody></tbody></table>
<p id="meta"></p>
<script type="module">
import { h, render } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
%%SIGNAL_IMPORT%%
const html = htm.bind(h);

function Counter() {
  const [n, setN] = useState(0);
  const click = () => { setN(n + 1); %%SIGNAL_CLICK%% };
  return html`<button onClick=${click}>clicked ${n} times</button>`;
}
render(html`<${Counter} />`, document.getElementById("app"));
%%SIGNAL_EFFECT%%

function cell(row, text) {
  const td = document.createElement("td");
  td.textContent = String(text);
  row.appendChild(td);
}

function renderTable() {
  const tbody = document.querySelector("#rt tbody");
  const entries = performance.getEntriesByType("resource")
    .filter(e => new URL(e.name).pathname.startsWith("/deps"));
  let sumTransfer = 0, sumEncoded = 0;
  for (const e of entries) {
    const u = new URL(e.name);
    const source = e.transferSize === 0 ? "cache"
                 : e.encodedBodySize === 0 ? "revalidated (304)"
                 : "network";
    sumTransfer += e.transferSize;
    sumEncoded += e.encodedBodySize;
    const row = tbody.insertRow();
    cell(row, u.pathname + u.search);
    cell(row, e.transferSize);
    cell(row, e.encodedBodySize);
    cell(row, e.decodedBodySize);
    cell(row, source);
    cell(row, e.duration.toFixed(1));
  }
  const total = tbody.insertRow();
  cell(total, "TOTAL (" + entries.length + " files)");
  cell(total, sumTransfer);
  cell(total, sumEncoded);
  cell(total, ""); cell(total, ""); cell(total, "");
  document.getElementById("meta").textContent =
    "Loaded " + new Date().toISOString() + " | " + navigator.userAgent;
}
addEventListener("load", renderTable);
</script>
</body>
</html>
"""

SIGNAL_IMPORT = 'import { signal, effect } from "@preact/signals-core";\nconst total = signal(0);'
SIGNAL_CLICK = "total.value++;"
SIGNAL_EFFECT = ('effect(() => { document.getElementById("sig").textContent = '
                 '"signal total: " + total.value; });')


def write_page(mode: str, label: str, imports: dict[str, str]) -> None:
    has_signals = "@preact/signals-core" in imports
    html = (PAGE
            .replace("%%MODE%%", mode)
            .replace("%%LABEL%%", label)
            .replace("%%IMPORTMAP%%", json.dumps({"imports": imports}, indent=2))
            .replace("%%SIGNAL_IMPORT%%", SIGNAL_IMPORT if has_signals else "")
            .replace("%%SIGNAL_CLICK%%", SIGNAL_CLICK if has_signals else "")
            .replace("%%SIGNAL_EFFECT%%", SIGNAL_EFFECT if has_signals else ""))
    out = SITE / f"{mode}-{label}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(html, encoding="utf-8")


LANDING = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dependency URL caching demo</title>
<style>body { font: 16px/1.5 system-ui, sans-serif; margin: 1.5rem; max-width: 46rem; }</style>
</head>
<body>
<h1>Global <code>?v=</code> vs content-addressed dependency URLs</h1>
<p>The same tiny app (preact + htm) in two lock versions: v2 adds one package
(@preact/signals-core). Each page lists its dependency requests from the
Resource Timing API; <code>transferSize</code> 0 means the file came from the browser cache.</p>
<ol>
  <li>Open DevTools, Network tab, leave "Disable cache" OFF.</li>
  <li>Baseline: <a href="/baseline-v1/">v1</a>, then <a href="/baseline-v2/">v2</a>.</li>
  <li>Redesign: <a href="/redesign-v1/">v1</a>, then <a href="/redesign-v2/">v2</a>.</li>
</ol>
<p>Static demo for a course assignment; not Lovable's system. Source code is in the linked repository.</p>
</body>
</html>
"""


def build(lock_path: Path, cache: dict) -> None:
    label = lock_path.stem.split(".")[-1]          # lock.v1.json -> "v1"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    canonical = json.dumps(lock["packages"], sort_keys=True, separators=(",", ":"))
    lockhash = hashlib.sha256(canonical.encode()).hexdigest()[:8]

    content_map, global_map = {}, {}
    for spec, entry in lock["imports"].items():
        pkg = entry["package"]
        pin = lock["packages"][pkg]
        key = (pkg, pin["version"])
        if key not in cache:
            cache[key] = download_package(pkg, pin["version"], pin["integrity"])
        data = extract_one(cache[key], entry["file"])

        digest = hashlib.sha256(data).hexdigest()[:16]
        (SITE / "deps" / f"{digest}.js").write_bytes(data)
        content_map[spec] = f"/deps/{digest}.js"

        flat = spec.replace("@", "").replace("/", "__")
        (SITE / "deps-global" / f"{flat}.js").write_bytes(data)
        global_map[spec] = f"/deps-global/{flat}.js?v={lockhash}"

        print(f"{label}  {spec:22} {len(data):>7} B  sha256/16={digest}")

    write_page("redesign", label, content_map)
    write_page("baseline", label, global_map)
    print(f"{label}  lockhash={lockhash}")


def main() -> None:
    if len(sys.argv) < 2:
        fail("usage: python3.12 resolver.py lock.v1.json [lock.v2.json ...]")
    for d in ["deps", "deps-global", *[p.name for p in SITE.glob("baseline-*")],
              *[p.name for p in SITE.glob("redesign-*")]]:
        shutil.rmtree(SITE / d, ignore_errors=True)
    (SITE / "deps").mkdir(parents=True)
    (SITE / "deps-global").mkdir()
    cache: dict = {}
    for arg in sys.argv[1:]:
        build(Path(arg), cache)
    (SITE / "index.html").write_text(LANDING, encoding="utf-8")


if __name__ == "__main__":
    main()
