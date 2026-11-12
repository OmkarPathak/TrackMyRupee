"""Performance tracking from numbers YOU enter (Instagram Insights). No Instagram API.

Optional fields may be left blank (stored as None and shown as "—"); nothing is inferred.
If *reach* is not entered, rates use *views* as the denominator and the row is labelled
basis="views" (Instagram's reach and views are different numbers — don't mix them casually).
"""
from collections import defaultdict
import datetime as dt

HOOK_STYLES = ["Question", "Number shock", "Comparison / puzzle", "Confession / POV", "Contrarian", "Challenge", "Story", "Mascot first-person"]
REQUIRED_ONE_OF = ("views", "reach")
FIELDS = ["views", "reach", "likes", "comments", "shares", "reposts", "saves", "avg_watch_s", "profile_visits", "link_clicks", "registrations"]
OPTIONAL = {"reach", "saves", "registrations", "reposts", "avg_watch_s", "comments"}   # blank allowed -> None
EXTRA = ["followers_pct", "non_followers_pct", "age_groups", "notes", "skip_rate_pct"]


def _num(v, name, optional):
    if v in (None, ""):
        if optional:
            return None
        return 0.0
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number")
    if x < 0:
        raise ValueError(f"{name} cannot be negative")
    return x


def normalize(d):
    """Validate one entry from the UI/CLI; returns the stored row (without id)."""
    row = {"content_id": d["content_id"], "published_date": d.get("published_date", ""), "hook_style": d.get("hook_style", ""), "notes": d.get("notes", "")}
    for k in FIELDS:
        row[k] = _num(d.get(k), k, k in OPTIONAL)
    if not (row["views"] or row["reach"]):
        raise ValueError("Enter views or reach (> 0) so rates can be computed")
    for k in ("followers_pct", "non_followers_pct", "skip_rate_pct"):
        v = _num(d.get(k), k, True)
        if v is not None and v > 100:
            raise ValueError(f"{k} cannot exceed 100")
        row[k] = v
    if row["followers_pct"] is not None and row["non_followers_pct"] is not None and abs(row["followers_pct"] + row["non_followers_pct"] - 100) > 0.6:
        raise ValueError("followers % + non-followers % should add up to ~100")
    ages = d.get("age_groups") or {}
    row["age_groups"] = {str(k): _num(v, f"age {k}", False) for k, v in ages.items()}
    if sum(row["age_groups"].values()) > 100.5:
        raise ValueError("age groups cannot add up to more than 100%")
    return row


def _r(a, b):
    return round(100 * a / b, 2) if b else None


def denom(t):
    return (t.get("reach") or 0) or (t.get("views") or 0)


def basis(t):
    return "reach" if (t.get("reach") or 0) else "views"


def rates(t, duration_s=None):
    d = denom(t); z = lambda k: t.get(k) or 0
    out = {"basis": basis(t),
           "engagement_rate": _r(z("likes") + z("comments") + z("shares") + z("reposts") + z("saves"), d),
           "share_rate": _r(z("shares"), d),
           "save_rate": _r(t["saves"], d) if t.get("saves") is not None else None,
           "profile_visit_rate": _r(z("profile_visits"), d),
           "ctr": _r(z("link_clicks"), d),
           "link_click_per_profile_visit": _r(z("link_clicks"), z("profile_visits")),
           "conversion_rate": _r(t["registrations"], z("link_clicks")) if t.get("registrations") is not None else None}
    if duration_s and t.get("avg_watch_s") is not None:
        out["avg_watch_pct"] = round(100 * t["avg_watch_s"] / duration_s, 1)
    return out


def duration_s(idea):
    try:
        return float(str(idea.get("duration", "")).rstrip("s"))
    except ValueError:
        return None


def _sum(rows):
    t = {k: sum(float(r.get(k) or 0) for r in rows) for k in FIELDS}
    t["avg_watch_s"] = round(sum(float(r.get("avg_watch_s") or 0) for r in rows) / max(1, sum(1 for r in rows if r.get("avg_watch_s") is not None)), 1)
    # use the same denominator rule per post, summed
    t["reach"] = sum(denom(r) for r in rows)
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
        ds = duration_s(i)
        if ds:
            dims["length"][_bucket(ds)].append(e)
        if e.get("published_date"):
            dims["weekday"][dt.date.fromisoformat(e["published_date"]).strftime("%A")].append(e)
    out = {}
    for d, groups in dims.items():
        out[d] = [{"key": k, "posts": len(v), **rates(_sum(v))} for k, v in sorted(groups.items())]
    return out


def suggestions(entries, cmp):
    if len(entries) < 3:
        return ["Add metrics for at least 3 published posts to unlock comparisons. Nothing is inferred from fewer data points."]
    tips = []
    for dim, label in (("pillar", "pillar"), ("hook_style", "hook style"), ("length", "video length"), ("format", "format")):
        rows = [r for r in cmp[dim] if r["posts"] >= 2 and r["key"] != "?" and r["engagement_rate"] is not None]
        if len(rows) >= 2:
            best, worst = max(rows, key=lambda r: r["engagement_rate"]), min(rows, key=lambda r: r["engagement_rate"])
            if best["engagement_rate"] > worst["engagement_rate"]:
                tips.append(f"By {label}, '{best['key']}' leads on engagement ({best['engagement_rate']}%) vs '{worst['key']}' ({worst['engagement_rate']}%) — small sample; verify.")
    tot = rates(_sum(entries))
    if tot["save_rate"] is not None and tot["share_rate"] is not None and tot["share_rate"] < tot["save_rate"]:
        tips.append("Saves outpace shares: useful but not yet 'send-to-a-friend' relatable — lean into Pillar A hooks.")
    if tot["ctr"] is not None and tot["ctr"] < 0.5:
        tips.append("Link CTR is low: make the CTA specific ('Try the free demo') and pin a comment repeating it.")
    return tips or ["No clear differences yet — keep publishing and entering data."]
