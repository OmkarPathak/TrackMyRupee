"""Performance tracking from numbers YOU enter. No Instagram API is used."""
from collections import defaultdict
import datetime as dt

HOOK_STYLES = ["Question", "Number shock", "Confession / POV", "Contrarian", "Challenge", "Story"]
FIELDS = ["views", "reach", "likes", "comments", "shares", "saves", "avg_watch_s", "profile_visits", "link_clicks", "registrations"]


def _r(a, b):
    return round(100 * a / b, 2) if b else 0.0


def rates(t):
    reach = t["reach"]
    return {"engagement_rate": _r(t["likes"] + t["comments"] + t["shares"] + t["saves"], reach), "share_rate": _r(t["shares"], reach),
            "save_rate": _r(t["saves"], reach), "profile_visit_rate": _r(t["profile_visits"], reach),
            "ctr": _r(t["link_clicks"], reach), "conversion_rate": _r(t["registrations"], t["link_clicks"])}


def _sum(rows):
    t = {k: sum(float(r.get(k) or 0) for r in rows) for k in FIELDS}
    t["avg_watch_s"] = round(sum(float(r.get("avg_watch_s") or 0) for r in rows) / len(rows), 1) if rows else 0
    return t


def _bucket(sec):
    return "≤20s" if sec <= 20 else "21–30s" if sec <= 30 else ">30s"


def compare(entries, ideas):
    idx = {i["id"]: i for i in ideas}
    dims = {"pillar": defaultdict(list), "format": defaultdict(list), "hook_style": defaultdict(list), "length": defaultdict(list), "weekday": defaultdict(list)}
    for e in entries:
        i = idx.get(e["content_id"], {})
        dims["pillar"][i.get("pillar", "?")].append(e)
        dims["format"][i.get("format", "?")].append(e)
        dims["hook_style"][e.get("hook_style") or "?"].append(e)
        try:
            dims["length"][_bucket(int(str(i.get("duration", "0")).rstrip("s")))].append(e)
        except ValueError:
            pass
        if e.get("published_date"):
            dims["weekday"][dt.date.fromisoformat(e["published_date"]).strftime("%A")].append(e)
    out = {}
    for d, groups in dims.items():
        out[d] = [{"key": k, "posts": len(v), **{kk: round(vv, 1) if kk in FIELDS else vv for kk, vv in _sum(v).items()}, **rates(_sum(v))} for k, v in sorted(groups.items())]
    return out


def suggestions(entries, cmp):
    if len(entries) < 3:
        return ["Add metrics for at least 3 published posts to unlock suggestions. Nothing is inferred from fewer data points."]
    tips = []
    for dim, label in (("pillar", "pillar"), ("hook_style", "hook style"), ("length", "video length"), ("format", "format")):
        rows = [r for r in cmp[dim] if r["posts"] >= 2 and r["key"] != "?"]
        if len(rows) >= 2:
            best, worst = max(rows, key=lambda r: r["engagement_rate"]), min(rows, key=lambda r: r["engagement_rate"])
            if best["engagement_rate"] > worst["engagement_rate"]:
                tips.append(f"By {label}, '{best['key']}' leads on engagement ({best['engagement_rate']}%) vs '{worst['key']}' ({worst['engagement_rate']}%) — consider more of the former (small sample; verify).")
    tot = rates(_sum(entries))
    if tot["share_rate"] < tot["save_rate"]:
        tips.append("Saves outpace shares: your content is useful but not yet 'send-to-a-friend' relatable — lean into Pillar A hooks.")
    if tot["conversion_rate"] and tot["ctr"] < 0.5:
        tips.append("Link CTR is low: make the CTA more specific ('Try the free demo') and pin a comment repeating it.")
    return tips or ["No clear differences yet — keep publishing and entering data."]
