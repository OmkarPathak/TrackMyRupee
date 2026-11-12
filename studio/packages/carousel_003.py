"""Carousel 003 (library idea carousel-003): "10 expenses you might be forgetting" — Hinglish.
Style: same as the posted 'Rupee Minis' carousel (measured; see render/carousel_rupee.py)."""

SLUG = "trackmyrupee_carousel_003_forgotten_expenses"

SLIDES = [
    {"kind": "cover", "pill": "Rupee Minis", "h1": ["Yeh 10 kharche", "yaad hain?"], "accent": ["Aksar chhoot jaate hain."],
     "body": ["Rent aur EMI toh yaad rehte hain.", "Baaki ka hisaab kaun rakhta hai?"],
     "note": "Cover — curiosity + checklist promise. Swipe cue bottom-left."},
    {"kind": "list", "h2": ["Saal mein ek baar", "aate hain:"],
     "cards": [("Life / health premium", "Saalana"), ("Gaadi ka insurance", "Saalana"), ("Annual app / domain plans", "Saalana")],
     "accent": ["Date yaad nahi, toh", "jhatka lagta hai."], "note": "Items 1–3 (annual)."},
    {"kind": "list", "h2": ["Har mahine chhote", "chhote kharche:"], "card_h": 100, "gap": 20,
     "cards": [("OTT, music, cloud plans", "Mahina"), ("Auto-pay jo bhool gaye", "Mahina"), ("Card fees aur late charges", "Kabhi"), ("FASTag aur parking", "Roz")],
     "accent": ["Ek-ek chhota hai,", "isliye yaad nahi."], "note": "Items 4–7 (small / recurring)."},
    {"kind": "list", "h2": ["Log aur mauke:"], "y0": 380,
     "cards": [("Festival aur gifts", "Season"), ("Dost ko UPI se diye paise", "Kabhi"), ("Doctor, dawai, tests", "Kabhi")],
     "accent": ["Yeh sab bhi ginti", "mein aate hain."], "note": "Items 8–10 (people & occasions)."},
    {"kind": "example", "h2": ["Chhota lagta hai,", "par saal ka total?"], "label": "Example (sirf samjhane ke liye)",
     "maths": "5 × ₹99 = ₹495 / mahina", "result": "₹5,940 / saal", "tag": "ILLUSTRATIVE EXAMPLE",
     "accent": ["Aapke kitne plans", "chal rahe hain?"], "note": "Maths: 5 × 99 = 495; 495 × 12 = 5,940. Teaser for the Sunday Reel."},
    {"kind": "steps", "h2": ["Kaise pakdein?"],
     "steps": [("Statement scan karo", "Pichle 2 mahine ka dekho."), ("Turant likh do", "Kharcha hote hi, yaad pe nahi."), ("Ek baar set karo", "Recurring kharche ek jagah.")],
     "note": "Practical action; product-neutral."},
    {"kind": "checks", "h2": ["TrackMyRupee mein:"],
     "items": ["Recurring ek baar set karo", "Renewal se 3 din pehle reminder", "Aane wale renewals ki list", ("Bank login, SMS nahi chahiye", True)],
     "note": "Verified: FEATURES.md ('Set it once, forget it' — reminder three days before, Renewing Soon list; no SMS/bank login)."},
    {"kind": "cta", "h2": ["Apna hisaab", "khud dekho."], "accent": ["Free demo, bina signup."], "button": "Free demo try karo", "url": "trackmyrupee.com/demo",
     "body": ["Save karo, month-end pe check karne ke liye.", "Sunday: ₹99 wale trap ka Reel."], "note": "CTA + Sunday Reel teaser."},
]


def _text(s):
    parts = []
    for k in ("pill", "h1", "h2", "accent", "body", "label", "maths", "result"):
        v = s.get(k)
        if isinstance(v, list):
            parts += v
        elif v:
            parts.append(v)
    parts += [c[0] for c in s.get("cards", [])] + [x if isinstance(x, str) else x[0] for x in s.get("items", [])]
    for st in s.get("steps", []):
        parts += list(st)
    return parts


PACKAGE = {
    "id": "carousel-003", "kind": "carousel", "version": 1, "status": "review", "slug": SLUG,
    "title": "Yeh 10 kharche yaad hain?",
    "language": "Hinglish (Roman script)",
    "strategy": {
        "pillar": "A — Relatable money problems (with a Pillar C product close)",
        "audience": "Salaried professionals 22–35 (and a Hinglish-comfortable audience)",
        "objective": "Saves and shares (checklist), a soft product mention, and a teaser to Sunday's Reel. Funnel: education → free demo.",
        "format": "8-slide carousel, 1080×1350, 'Rupee Minis' style matching the posted carousel",
        "link_to_reel": "Sunday Reel: 'The ₹99 subscription trap' (English).",
        "feature": "Recurring entries + reminder three days before + Renewing Soon list; privacy (no bank login / SMS) — verified in FEATURES.md",
    },
    "hooks": {"selected": 0, "options": ["Yeh 10 kharche yaad hain?", "10 kharche jo aap har saal bhool jaate ho", "Rent aur EMI yaad hain. Baaki?", "Aapke bank statement mein yeh 10 chhupe hain", "Kya aapne yeh 10 kharche ginne?"]},
    "slides": [{"n": i + 1, "kind": s["kind"], "headline": " ".join(s.get("h1") or s.get("h2") or [s.get("accent", [""])[0]]), "body": " · ".join(_text(s)),
                "visual": s["note"], "layout": f"type={s['kind']}; left-aligned at x=120; flat #FBF9F5; handle @trackmyrupee bottom-left",
                "background": "#FBF9F5", "typography": "Plus Jakarta Sans ExtraBold (tight tracking) headlines; Italic coral/green accent; Medium body"} for i, s in enumerate(SLIDES)],
    "publishing": {
        "caption_hook": "Yeh 10 kharche yaad hain? 🤔",
        "caption": "Yeh 10 kharche yaad hain? 🤔\n\nRent aur EMI toh yaad rehte hain. Dikkat un kharchon se hoti hai jo saal mein ek baar aate hain, ya itne chhote hote hain ki dhyan hi nahi jaata.\n\n📅 Saalana: life/health premium, gaadi ka insurance, annual app/domain plans\n🔁 Chhote-chhote: OTT-music-cloud plans, bhoole hue auto-pay, card fees aur late charges, FASTag aur parking\n🎉 Mauke: festival aur gifts, dost ko UPI se diye paise, doctor-dawai-tests\n\nEk-ek chhota lagta hai, par saal ka total chhota nahi hota. (Slide 5 ka example sirf samjhane ke liye hai.)\n\nTrackMyRupee mein recurring kharche ek baar set karo — renewal se 3 din pehle reminder aata hai. Bank login ya SMS access ki zaroorat nahi.\n\n👉 Free demo try karo — link in bio.\n💾 Month-end pe check karne ke liye save karo.\n📩 Us dost ko bhejo jiska har mahine ka sawaal hai: \"paisa kahan gaya?\"\n\nSunday ko Reel: ₹99 wala subscription trap.\n\nIllustrative numbers. Not financial advice.",
        "cta": "Free demo try karo — link in bio",
        "hashtags_core": ["#PersonalFinanceIndia", "#ExpenseTracker", "#MoneyManagement", "#FinancialLiteracy", "#TrackMyRupee"],
        "hashtags_extended": ["#BudgetingTips", "#IndianFinance"],
        "hashtag_note": "Use the 5 core tags + at most 2 extended.",
        "pinned_comment": "Aapka sabse zyada bhoola hua kharcha kaunsa hai? 👇 (Insurance, subscriptions, ya kuch aur?)",
        "story_text": ["Story 1 (swipe-up style sticker on slide 1): 'Yeh 10 kharche yaad hain?'", "Story 2 (poll): 'Kitne subscriptions chal rahe hain aapke?' — 1–3 / 4–6 / Pata nahi", "Story 3 (link sticker): 'Free demo — trackmyrupee.com/demo'"],
        "alt_text": "Hinglish carousel listing 10 expenses people forget: annual premiums and renewals, small monthly plans and charges, and festival, UPI and medical spends, with a TrackMyRupee reminder tip.",
    },
    "asset_checklist": [{"item": "8 slide PNGs 1080×1350", "status": "done", "file": "slide_01.png … slide_08.png"}, {"item": "Carousel PDF", "status": "done", "file": "carousel.pdf"},
                        {"item": "Contact sheet", "status": "done", "file": "contact_sheet.png"}],
    "fact_check_flags": [],
    "learning_log": {"approved": False, "notes": []},
}
