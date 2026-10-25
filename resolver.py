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


def write_page(mode: str, label: str, imports: dict[str, str]) -> None:
    importmap = json.dumps({"imports": imports}, indent=2)
    lines = [f"import * as m{i} from {json.dumps(s)};" for i, s in enumerate(imports)]
    names = ", ".join(json.dumps(s) for s in imports)
    mods = ", ".join(f"m{i}" for i in range(len(imports)))
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{mode} {label}</title>
<script type="importmap">
{importmap}
</script>
</head>
<body>
<h1>{mode} / {label}</h1>
<pre id="out">loading...</pre>
<script type="module">
{chr(10).join(lines)}
const names = [{names}];
const mods = [{mods}];
document.getElementById("out").textContent =
  names.map((n, i) => n + ": " + Object.keys(mods[i]).length + " exports").join("\\n");
</script>
</body>
</html>
"""
    out = SITE / f"{mode}-{label}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(html, encoding="utf-8")


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


if __name__ == "__main__":
    main()
