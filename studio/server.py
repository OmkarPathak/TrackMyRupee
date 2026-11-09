"""TrackMyRupee Instagram Content Studio — local Django server.

    python studio/server.py            # http://127.0.0.1:8765

Single-user local tool: binds to 127.0.0.1, no auth, no database (JSON files in
studio/data). API keys are read from the environment only and never sent to the
browser. Reuses the repo's Django install; independent of the production app."""
import json, os, random, re, secrets, subprocess, sys, threading, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
except Exception:
    pass

import django
from django.conf import settings

if not settings.configured:
    settings.configure(DEBUG=os.environ.get("STUDIO_DEBUG") == "1", SECRET_KEY=os.environ.get("STUDIO_SECRET_KEY") or secrets.token_hex(16),
                       ROOT_URLCONF=__name__, ALLOWED_HOSTS=["127.0.0.1", "localhost"], MIDDLEWARE=[], DATABASES={},
                       DATA_UPLOAD_MAX_MEMORY_SIZE=60 * 1024 * 1024, USE_TZ=True)
    django.setup()

from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.urls import path
from django.views.decorators.http import require_http_methods

from core import analytics, exporter, library, providers, qc, store

store.init()
WEB = ROOT / "web"
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def body(req):
    try:
        return json.loads(req.body or b"{}")
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON")


def api(fn):
    def w(req, *a, **k):
        try:
            return fn(req, *a, **k)
        except KeyError as e:
            return JsonResponse({"error": f"Not found: {e}"}, status=404)
        except ValueError as e:
            return JsonResponse({"error": str(e)}, status=400)
        except Exception as e:  # surface, don't hide
            return JsonResponse({"error": f"{type(e).__name__}: {e}"}, status=500)
    w.__name__ = fn.__name__
    return w


def idea(iid):
    for i in store.read("ideas"):
        if i["id"] == iid:
            return i
    raise KeyError(iid)


# ---------------- pages / files --------------------------------------------
def index(req):
    return FileResponse(open(WEB / "index.html", "rb"), content_type="text/html; charset=utf-8")


def webfile(req, name):
    p = (WEB / name).resolve()
    if WEB not in p.parents or not p.is_file():
        raise Http404
    return FileResponse(open(p, "rb"))


def fontfile(req, name):
    p = (ROOT / "assets/fonts" / SAFE_NAME.sub("", name)).resolve()
    if not p.is_file():
        raise Http404
    return FileResponse(open(p, "rb"))


def outfile(req, slug, name):
    p = (exporter.OUT / slug / name).resolve()
    if exporter.OUT.resolve() not in p.parents or not p.is_file():
        raise Http404
    return FileResponse(open(p, "rb"))  # supports Range via FileResponse for video seeking


def upfile(req, cid, name):
    p = (store.DATA / "uploads" / cid / name).resolve()
    if (store.DATA / "uploads").resolve() not in p.parents or not p.is_file():
        raise Http404
    return FileResponse(open(p, "rb"))


# ---------------- state / ideas --------------------------------------------
@api
def state(req):
    ideas = store.read("ideas"); cal = store.read("calendar"); assets = store.read("assets", [])
    reels = [i for i in ideas if i["kind"] == "reel"]; cars = [i for i in ideas if i["kind"] == "carousel"]
    pk = {i["id"] for i in ideas if store.package(i["id"])}
    by_pillar = {p: sum(1 for i in ideas if i["pillar"] == p) for p in library.PILLARS}
    return JsonResponse({"stats": {"ideas": len(ideas), "scripts": len(pk),
        "reels_completed": sum(1 for i in reels if i["status"] in ("Approved", "Published")),
        "carousels_completed": sum(1 for i in cars if i["status"] in ("Approved", "Published")),
        "pending_assets": sum(1 for pid in pk for a in store.package(pid).get("asset_checklist", []) if a["status"] == "pending"),
        "published": sum(1 for i in ideas if i["status"] == "Published"), "by_pillar": by_pillar, "assets": len(assets)},
        "pillars": library.PILLARS, "statuses": library.STATUSES, "formats": library.FORMATS, "hook_styles": analytics.HOOK_STYLES,
        "packages": sorted(pk), "providers": providers.status(), "brand": store.read("brand")})


@api
@require_http_methods(["GET", "POST"])
def ideas_view(req):
    ideas = store.read("ideas")
    if req.method == "GET":
        return JsonResponse(ideas, safe=False)
    d = body(req)
    title = (d.get("title") or "").strip()
    if not title:
        raise ValueError("Title is required")
    if d.get("pillar") not in library.PILLARS:
        raise ValueError("Pick a valid pillar")
    kind = d.get("kind", "reel")
    n = max([i["no"] for i in ideas if i["kind"] == kind] + [0]) + 1
    new = {"id": f"{kind}-{n:03d}", "kind": kind, "no": n, "title": title, "pillar": d["pillar"], "format": d.get("format", "Motion graphic"),
           "duration": d.get("duration", "30s"), "feature": d.get("feature", ""), "concept": d.get("concept", ""), "status": "Idea", "series": d.get("series")}
    ideas.append(new); store.write("ideas", ideas)
    return JsonResponse(new, status=201)


@api
@require_http_methods(["PATCH", "DELETE", "POST"])
def idea_view(req, iid, action=None):
    ideas = store.read("ideas"); i = next((x for x in ideas if x["id"] == iid), None)
    if not i:
        raise KeyError(iid)
    if req.method == "DELETE":
        store.write("ideas", [x for x in ideas if x["id"] != iid]); return JsonResponse({"ok": True})
    if action == "duplicate":
        n = max(x["no"] for x in ideas if x["kind"] == i["kind"]) + 1
        c = {**i, "id": f"{i['kind']}-{n:03d}", "no": n, "title": i["title"] + " (copy)", "status": "Idea"}
        ideas.append(c); store.write("ideas", ideas)
        pk = store.package(iid)
        if pk:
            store.write(f"packages/{c['id']}", {**pk, "id": c["id"], "title": c["title"], "status": "idea", "slug": f"trackmyrupee_{c['id'].replace('-', '_')}_copy"})
        return JsonResponse(c, status=201)
    d = body(req)
    for k in ("title", "pillar", "format", "duration", "feature", "concept", "series"):
        if k in d:
            i[k] = d[k]
    if "status" in d:
        if d["status"] not in library.STATUSES:
            raise ValueError("Unknown status")
        i["status"] = d["status"]
        for c in (cal := store.read("calendar")):
            if c["content_id"] == iid:
                c["status"] = d["status"]
        store.write("calendar", cal)
    store.write("ideas", ideas)
    return JsonResponse(i)


# ---------------- packages --------------------------------------------------
@api
@require_http_methods(["GET", "PUT"])
def package_view(req, iid):
    i = idea(iid)
    pk = store.package(iid)
    if req.method == "PUT":
        if not pk:
            raise KeyError("package")
        d = body(req)
        if not isinstance(d, dict) or "scenes" not in d or "publishing" not in d:
            raise ValueError("Invalid package")
        store.write(f"packages/{iid}", d); pk = d
    if not pk:
        return JsonResponse({"package": None, "idea": i})
    res = qc.run(pk)
    out = exporter.OUT / (pk.get("slug") or "")
    files = sorted(p.name for p in out.iterdir() if p.is_file()) if out.is_dir() else []
    return JsonResponse({"package": pk, "idea": i, "qc": res, "qc_summary": qc.summary(res), "files": files, "slug": pk.get("slug")})


@api
@require_http_methods(["POST"])
def generate(req, iid):
    i = idea(iid)
    if store.package(iid):
        return JsonResponse({"status": "exists"})
    prompt = (f"Create {i['kind']} {i['no']}: \"{i['title']}\" (pillar {i['pillar']} — {library.PILLARS[i['pillar']]}; format: {i['format']}; "
              f"~{i.get('duration','')}). Brief: {i.get('concept','')} Follow the approval workflow: one piece only, then wait for my feedback.")
    return JsonResponse({"status": "needs-authoring", "message": "This studio does not auto-write scripts with a hidden model. In this workflow Claude authors each piece "
                         "one at a time (so quality and your approval stay in the loop). Copy the request below into your Claude Code session.", "request": prompt})


@api
@require_http_methods(["POST"])
def regen(req, iid, what):
    pk = store.package(iid)
    if not pk:
        raise KeyError("package")
    d = body(req)
    pub = pk["publishing"]
    if what == "hooks":
        pillar = pk["strategy"]["pillar"][0]
        bank = [h["text"] for h in store.read("hooks") if h["pillar"] == pillar and h["text"] not in pk["hooks"]["options"]]
        pk["hooks"]["alternatives"] = random.sample(bank, min(5, len(bank)))
    elif what == "cta":
        cta = (d.get("cta") or "").strip()
        if not cta:
            raise ValueError("CTA required")
        old = pub["cta"]; pub["cta"] = cta
        pub["caption"] = pub["caption"].replace(old, cta) if old in pub["caption"] else pub["caption"] + "\n" + cta
    elif what == "caption":
        ctas = store.read("ctas"); pub["cta"] = random.choice(ctas)
        pub["caption"] = re.sub(r"👉 .*", "👉 " + pub["cta"] + " No bank login. No SMS access.", pub["caption"], count=1)
        ext = pub["hashtags_extended"][:]; random.shuffle(ext); pub["hashtags_extended"] = ext
    else:
        raise KeyError(what)
    store.write(f"packages/{iid}", pk)
    return JsonResponse({"package": pk})


@api
@require_http_methods(["POST"])
def approve(req, iid):
    """Record creative preferences when the director approves (Phase 3). Only the
    director's explicit action in the UI triggers this."""
    d = body(req)
    if d.get("confirm") != "APPROVED":
        raise ValueError("Approval must be explicit")
    pk = store.package(iid)
    if not pk:
        raise KeyError("package")
    pk["status"] = "approved"; pk["learning_log"] = {"approved": True, "notes": d.get("notes", [])}
    store.write(f"packages/{iid}", pk)
    ideas = store.read("ideas")
    for i in ideas:
        if i["id"] == iid:
            i["status"] = "Approved"
    store.write("ideas", ideas)
    brand = store.read("brand"); brand["approved_style"] = d.get("style") or brand.get("approved_style"); store.write("brand", brand)
    return JsonResponse({"ok": True})


_jobs = {}


@api
@require_http_methods(["POST", "GET"])
def render_view(req, iid):
    pk = store.package(iid)
    if not pk or iid != "reel-001":
        raise ValueError("A render pipeline exists only for packages with a renderer (currently reel-001).")
    if req.method == "GET":
        return JsonResponse(_jobs.get(iid, {"state": "idle"}))
    if _jobs.get(iid, {}).get("state") == "running":
        return JsonResponse(_jobs[iid])
    _jobs[iid] = {"state": "running"}

    def run():
        r = subprocess.run([sys.executable, str(ROOT / "build_reel_001.py")], capture_output=True, text=True)
        _jobs[iid] = {"state": "done" if r.returncode == 0 else "error", "log": (r.stdout + r.stderr)[-1500:]}
    threading.Thread(target=run, daemon=True).start()
    return JsonResponse(_jobs[iid])


@api
def export_zip(req, iid):
    pk = store.package(iid)
    if not pk:
        raise ValueError("Nothing to export yet — this idea has no developed package.")
    data, name = exporter.zip_bytes(pk)
    r = HttpResponse(data, content_type="application/zip"); r["Content-Disposition"] = f'attachment; filename="{name}.zip"'
    return r


# ---------------- calendar / brand / library --------------------------------
@api
@require_http_methods(["GET", "PATCH"])
def calendar_view(req, day=None, fmt=None):
    cal = store.read("calendar")
    if req.method == "GET":
        return JsonResponse(cal, safe=False)
    d = body(req)
    row = next((c for c in cal if c["day"] == day and c["format"] == fmt), None)
    if not row:
        raise KeyError("calendar row")
    for k in ("date", "title", "cta", "assets", "audience", "objective", "story"):
        if k in d:
            row[k] = d[k]
    if "status" in d:
        if d["status"] not in library.STATUSES:
            raise ValueError("Unknown status")
        row["status"] = d["status"]
        ideas = store.read("ideas")
        for i in ideas:
            if i["id"] == row["content_id"]:
                i["status"] = d["status"]
        store.write("ideas", ideas)
    store.write("calendar", cal)
    return JsonResponse(row)


@api
@require_http_methods(["GET", "PUT"])
def lib_view(req, name):
    if name not in ("brand", "hooks", "ctas", "caption_templates", "hashtag_templates", "series"):
        raise KeyError(name)
    if req.method == "PUT":
        store.write(name, body(req))
    return JsonResponse(store.read(name), safe=False)


# ---------------- assets ---------------------------------------------------
ALLOWED = {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".mp3", ".wav", ".m4a", ".srt", ".pdf"}


@api
@require_http_methods(["GET", "POST", "DELETE"])
def assets_view(req, aid=None):
    assets = store.read("assets", [])
    if req.method == "GET":
        return JsonResponse(assets, safe=False)
    if req.method == "DELETE":
        a = next((x for x in assets if x["id"] == aid), None)
        if not a:
            raise KeyError(aid)
        Path(a["path"]).unlink(missing_ok=True); store.write("assets", [x for x in assets if x["id"] != aid])
        return JsonResponse({"ok": True})
    f = req.FILES.get("file"); cid = req.POST.get("content_id", "")
    if not f:
        raise ValueError("No file")
    idea(cid)
    ext = Path(f.name).suffix.lower()
    if ext not in ALLOWED:
        raise ValueError(f"File type {ext or '(none)'} not allowed")
    name = SAFE_NAME.sub("_", Path(f.name).name)
    d = store.DATA / "uploads" / cid; d.mkdir(parents=True, exist_ok=True)
    p = d / name
    with open(p, "wb") as fh:
        for ch in f.chunks():
            fh.write(ch)
    a = {"id": uuid.uuid4().hex[:10], "content_id": cid, "name": name, "path": str(p), "size": p.stat().st_size, "label": req.POST.get("label", ""), "url": f"/uploads/{cid}/{name}"}
    assets.append(a); store.write("assets", assets)
    return JsonResponse(a, status=201)


# ---------------- performance ----------------------------------------------
@api
@require_http_methods(["GET", "POST", "DELETE"])
def metrics_view(req, mid=None):
    ms = store.read("metrics", [])
    if req.method == "DELETE":
        store.write("metrics", [m for m in ms if m["id"] != mid]); return JsonResponse({"ok": True})
    if req.method == "POST":
        d = body(req); idea(d.get("content_id", ""))
        row = {"id": uuid.uuid4().hex[:8], **analytics.normalize(d)}
        ms.append(row); store.write("metrics", ms)
    ideas = store.read("ideas")
    idx = {i["id"]: i for i in ideas}
    for m in ms:
        m["rates"] = analytics.rates(m, analytics.duration_s(idx.get(m["content_id"], {})))
    cmp = analytics.compare(ms, ideas)
    return JsonResponse({"entries": ms, "compare": cmp, "suggestions": analytics.suggestions(ms, cmp)})


urlpatterns = [
    path("", index), path("web/<path:name>", webfile), path("fonts/<str:name>", fontfile),
    path("files/<str:slug>/<str:name>", outfile), path("uploads/<str:cid>/<str:name>", upfile),
    path("api/state", state), path("api/ideas", ideas_view), path("api/ideas/<str:iid>", idea_view),
    path("api/ideas/<str:iid>/<str:action>", idea_view),
    path("api/package/<str:iid>", package_view), path("api/generate/<str:iid>", generate),
    path("api/package/<str:iid>/regen/<str:what>", regen), path("api/package/<str:iid>/approve", approve),
    path("api/render/<str:iid>", render_view), path("api/export/<str:iid>.zip", export_zip),
    path("api/calendar", calendar_view), path("api/calendar/<int:day>/<str:fmt>", calendar_view),
    path("api/lib/<str:name>", lib_view),
    path("api/assets", assets_view), path("api/assets/<str:aid>", assets_view),
    path("api/metrics", metrics_view), path("api/metrics/<str:mid>", metrics_view),
]

if __name__ == "__main__":
    from django.core.management import execute_from_command_line
    port = os.environ.get("STUDIO_PORT", "8765")
    execute_from_command_line([sys.argv[0], "runserver", f"127.0.0.1:{port}", "--noreload"])
