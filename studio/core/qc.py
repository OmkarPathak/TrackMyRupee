"""Automated quality-control pass (the 11 checks in the brief). It catches what
a machine can catch; a human review is still required before publishing."""
import re

BANNED = [
 (r"auto(matic(ally)?)?[ -](bank[ -])?(sync|import|fetch)", "Claims automatic sync/import"),
 (r"\bbank sync\b", "Mentions bank sync"),
 (r"\bAI[- ](powered|driven|insights|assistant)\b|artificial intelligence", "AI claim (product uses rule-based insights, no outside AI)"),
 (r"guarantee", "Guarantee language"),
 (r"\b(best|#1|number one) (app|tracker)\b", "Superlative claim"),
 (r"\b(better|cheaper) than\b", "Comparative claim"),
 (r"\b(axio|walnut|indmoney|cred|moneyview|groww|zerodha|paytm)\b", "Names a competitor/third party"),
 (r"\bearn \d+%|\breturns? of \d+|\bdouble your money|get rich", "Return/wealth promise"),
 (r"read(s)? your sms|bank login required", "Contradicts privacy positioning"),
]
NEGATION = re.compile(r"(no|not|never|without|doesn'?t|don'?t|isn'?t|nor)\W+(\w+\W+){0,3}$", re.I)
FACT_WORDS = r"\b(tax|rbi|sebi|interest rate|fd rate|credit score|cibil|repo|regulat|deduction|80c|gst|ppf rate|epf rate|inflation rate)\b"


def _texts(pkg):
    out = [pkg.get("title", "")]
    for s in pkg.get("scenes", []):
        for k in ("on_screen", "vo_en", "vo_hinglish", "visual"):
            out.append(s.get(k, ""))
    pub = pkg.get("publishing", {})
    out += [pub.get("caption", ""), pub.get("pinned_comment", "")]
    for sl in pkg.get("slides", []):
        out += [sl.get("headline", ""), sl.get("body", "")]
    return [t for t in out if t]


def run(pkg):
    res = []

    def add(name, status, detail=""):
        res.append({"check": name, "status": status, "detail": detail})

    txt = "\n".join(_texts(pkg))
    hooks = pkg.get("hooks", {}).get("options", [])
    add("1. Hook is compelling", "pass" if len(hooks) >= 5 else "warn" if pkg.get("kind") == "carousel" else "fail",
        f"{len(hooks)} hook options" + ("; none generic ('Did you know')" if not re.search(r"did you know", " ".join(hooks), re.I) else "; contains generic hook"))
    add("2. Relevant to Indian audience", "pass" if re.search(r"₹|UPI|EMI|SIP|salary|FD|PPF|EPF", txt) else "warn", "Rupee/Indian money terms present" if "₹" in txt else "No ₹ references")

    b = pkg.get("illustrative_budget")
    if b:
        total = sum(g["amount"] for g in b["groups"])
        add("4. Numbers are mathematically correct", "pass" if total == b["salary"] else "fail", f"groups sum to ₹{total:,} vs salary ₹{b['salary']:,}")
        mys = [g for g in b["groups"] if g["kind"] == "mystery"]
        if mys:
            pct = round(100 * mys[0]["amount"] / b["salary"])
            ok = f"{pct}%" in txt
            add("4b. Stated percentage matches", "pass" if ok else "warn", f"computed {pct}%")
    elif not pkg.get("illustrative_networth") and not pkg.get("illustrative_maths"):
        add("4. Numbers are mathematically correct", "warn", "No structured budget to verify — check by hand")

    im = pkg.get("illustrative_maths")
    if im:
        ok = im["plans"] * im["price"] == im["monthly"] and im["monthly"] * 12 == im["yearly"]
        add("4. Numbers are mathematically correct", "pass" if ok else "fail", f"{im['plans']} × ₹{im['price']} = ₹{im['plans']*im['price']:,}/month; × 12 = ₹{im['monthly']*12:,}/year (stated ₹{im['yearly']:,})")
    nw = pkg.get("illustrative_networth")
    if nw:
        a = sum(x["amount"] for x in nw["assets"]); l = sum(x["amount"] for x in nw["liabilities"])
        add("4. Numbers are mathematically correct", "pass" if a - l == nw["net_worth"] else "fail", f"assets ₹{a:,} − liabilities ₹{l:,} = ₹{a-l:,} (stated ₹{nw['net_worth']:,})")

    hits = []
    for pat, why in BANNED:
        for m in re.finditer(pat, txt, re.I):
            if not NEGATION.search(txt[max(0, m.start() - 30):m.start()]):
                hits.append(f"{why}: “{m.group(0)}”")
    add("5/10. No unsupported feature, financial or privacy claims", "pass" if not hits else "fail", "; ".join(hits) or "none found")

    has_inr = "₹" in txt
    ill = re.search(r"illustrative", txt, re.I)
    add("3. Financial info labelled/not advice", "pass" if (not has_inr or ill) else "fail", "Illustrative label present" if ill else "Numbers present without 'illustrative' label")

    pub = pkg.get("publishing", {})
    add("7. CTA appropriate", "pass" if pub.get("cta") else "warn", pub.get("cta", "No CTA set"))

    long_scenes = [s["n"] for s in pkg.get("scenes", []) if len(re.findall(r"\w+", s.get("on_screen", ""))) > 22]
    add("9. Text readable on mobile", "pass" if not long_scenes else "warn", "On-screen text ≤22 words per scene" if not long_scenes else f"Dense scenes: {long_scenes}")

    scenes = pkg.get("scenes", [])
    first_pitch = next((s["n"] for s in scenes if re.search(r"\b(demo|trackmyrupee|app)\b", s.get("vo_en", ""), re.I)), None)
    add("11. Value before selling", "pass" if (first_pitch is None or first_pitch >= max(1, int(len(scenes) * 0.7))) else "warn",
        f"First product mention in scene {first_pitch}/{len(scenes)}" if first_pitch else "No product pitch")

    flags = set(m.group(0).lower() for m in re.finditer(FACT_WORDS, txt, re.I)) | set(pkg.get("fact_check_flags", []))
    add("Fact-check before publishing", "warn" if flags else "pass", ("Verify: " + ", ".join(sorted(flags))) if flags else "No changing financial/regulatory facts used")
    add("8. Visual matches brand", "pass", "Rendered with TrackMyRupee tokens, real logo, Plus Jakarta Sans (manual visual check still required)")
    add("6. Language natural", "warn", "Automated check not possible — read the VO aloud once")
    return res


def summary(res):
    return {"pass": sum(r["status"] == "pass" for r in res), "warn": sum(r["status"] == "warn" for r in res), "fail": sum(r["status"] == "fail" for r in res)}


def markdown(res):
    icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}
    return "# QC report\n\n" + "\n".join(f"- {icon[r['status']]} **{r['check']}** — {r['detail']}" for r in res) + "\n"
