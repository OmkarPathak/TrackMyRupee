"""Tiny JSON-file persistence (no database needed). Atomic writes, one lock."""
import json, os, tempfile, threading, importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("STUDIO_DATA_DIR", ROOT / "data"))
_lock = threading.RLock()


def _p(name):
    return DATA / f"{name}.json"


def read(name, default=None):
    with _lock:
        f = _p(name)
        if not f.exists():
            return default
        return json.loads(f.read_text("utf-8"))


def write(name, obj):
    with _lock:
        f = _p(name); f.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=f.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, f)


def init():
    """Seed on first run; load shipped packages (never overwrite edits)."""
    from core.library import seed
    with _lock:
        for k, v in seed().items():
            if read(k) is None:
                write(k, v)
        if read("metrics") is None:
            write("metrics", [])
        if read("assets") is None:
            write("assets", [])
        pk = ROOT / "packages"
        for f in sorted(pk.glob("*.py")):
            spec = importlib.util.spec_from_file_location(f.stem, f)
            m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
            pid = m.PACKAGE["id"]
            cur = read(f"packages/{pid}")
            if cur is not None and cur.get("version", 1) < m.PACKAGE.get("version", 1):
                write(f"packages/{pid}.v{cur.get('version', 1)}-backup", cur)  # keep your edits
                cur = None
            if cur is None:
                write(f"packages/{pid}", {**m.PACKAGE, "slug": m.SLUG})
                ideas = read("ideas")
                for i in ideas:
                    if i["id"] == pid:
                        i["status"] = m.PACKAGE["status"].capitalize() if m.PACKAGE["status"] != "review" else "Review"
                write("ideas", ideas)


def package(pid):
    return read(f"packages/{pid}")
