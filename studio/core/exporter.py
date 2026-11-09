"""Turn a stored package into the production folder / ZIP."""
import io, zipfile
from pathlib import Path
from core import qc, store

OUT = store.ROOT / "output"


def _srt_time(sec):
    ms = int(round(sec * 1000)); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def _range(t):
    a, b = t.replace("–", "-").split("-"); return float(a), float(b)


def srt(pkg, key="vo_en"):
    out = []
    for i, s in enumerate(pkg["scenes"], 1):
        a, b = _range(s["t"]); out.append(f"{i}\n{_srt_time(a + .2)} --> {_srt_time(b - .1)}\n{s[key]}\n")
    return "\n".join(out)


def _illustrative(pkg):
    if pkg.get("illustrative_budget"):
        return "## Illustrative budget (labelled on screen)\n" + "".join(f"- {g['name']}: ₹{g['amount']:,}\n" for g in pkg["illustrative_budget"]["groups"])
    n = pkg.get("illustrative_networth")
    if n:
        return ("## Illustrative net worth (labelled on screen)\n" + "".join(f"- {a['name']}: ₹{a['amount']:,}\n" for a in n["assets"]) +
                "".join(f"- {a['name']}: −₹{a['amount']:,}\n" for a in n["liabilities"]) + f"- **Net worth: ₹{n['net_worth']:,}**\n")
    return ""


def build_files(pkg):
    """Return {filename: text} for a Reel package."""
    st, pub = pkg["strategy"], pkg["publishing"]
    f = {}
    f["script.md"] = f"# {pkg['title']}\n\n## Strategy\n" + "".join(f"- **{k.replace('_', ' ').title()}:** {v}\n" for k, v in st.items()) + \
        "\n## Hooks\n" + "".join(f"{i+1}. {h}{'  ← selected' if i == pkg['hooks']['selected'] else ''}\n" for i, h in enumerate(pkg["hooks"]["options"])) + \
        f"\n_{pkg['hooks'].get('note','')}_\n\n" + _illustrative(pkg) + \
        "\n## Scene script\n" + "".join(f"\n### Scene {s['n']} · {s['t']}s · {s['name']}\n- **On-screen:** {s['on_screen']}\n- **VO (EN):** {s['vo_en']}\n- **SFX:** {s['sfx']}\n- **Transition:** {s['transition']}\n" for s in pkg["scenes"]) + \
        f"\n## Music\n{pkg['music']['direction']}\n\n{pkg['music']['structure']}\n\n_{pkg['music']['licensing']}_\n\n## CTA\n{pub['cta']}\n"
    f["storyboard.md"] = f"# Storyboard — {pkg['title']}\n" + "".join(
        f"\n## Scene {s['n']} — {s['name']} ({s['t']}s)\n| Field | Detail |\n|---|---|\n| Visual | {s['visual']} |\n| Camera | {s['camera']} |\n| Character | {'None on screen (motion graphics)' } |\n| Background | {s['background']} |\n| Lighting | {s['lighting']} |\n| Movement | {s['movement']} |\n| On-screen text | {s['on_screen']} |\n| Voiceover | {s['vo_en']} |\n| Transition | {s['transition']} |\n" for s in pkg["scenes"])
    v = pkg["voiceover"]
    f["voiceover.txt"] = f"DIRECTION\n{v['direction']}\n\n=== ENGLISH (Indian) ===\n{v['en']}\n\n=== HINGLISH ===\n{v['hinglish']}\n\n=== HINDI ===\n{v['hindi']}\n\nSTATUS: {v['tts_status']}\n"
    c = pkg["character"]
    f["image-prompts.md"] = f"# Image prompts (optional inserts)\n\n_{c['note']}_\n\n## Character bible — paste verbatim in every prompt\n**{c['name']}** — {c['bible']}\n\n### Consistency rules\n" + "".join(f"- {r}\n" for r in c["consistency_rules"]) + \
        "\n## Prompts\n" + "".join(f"\n### {p['id']} — {p['use']}\n```\n{p['prompt']}\n```\nNegative: {p['negative']}\n" for p in pkg["image_prompts"]) + \
        "\n## Global negatives\ndistorted hands, incorrect currency symbols, gibberish text, fake app interfaces, watermarks, unrealistic people, futuristic effects, foreign-looking stock photography.\n"
    f["video-prompts.md"] = "# Image-to-video movement prompts\n" + "".join(f"\n## {p['id']} (from {p['from']}, {p['duration']})\n{p['prompt']}\n" for p in pkg["video_prompts"]) + \
        "\n_Real TrackMyRupee UI must come from the supplied screenshot/screen recording — never from a video generator._\n"
    f["caption.txt"] = pub["caption"] + "\n\n" + " ".join(pub["hashtags_core"]) + "\n\n--- First line ---\n" + pub["caption_hook"] + \
        "\n\n--- Pinned comment ---\n" + pub["pinned_comment"] + "\n\n--- Story text ---\n" + "\n".join(pub["story_text"]) + "\n\n--- Alt text ---\n" + pub["alt_text"] + "\n"
    f["hashtags.txt"] = "CORE: " + " ".join(pub["hashtags_core"]) + "\nEXTENDED (pick ≤3): " + " ".join(pub["hashtags_extended"]) + "\n" + pub["hashtag_note"] + "\n"
    e = pkg["editing"]
    f["editing-instructions.md"] = f"# Editing instructions (CapCut)\n\n**Tool:** {e['tool']}\n\n**Canvas:** {e['canvas']}\n\n## Timeline\n" + "".join(f"- {t}\n" for t in e["timeline"]) + \
        f"\n## Text placement\n{e['text_placement']}\n\n## Zoom & movement\n{e['zoom_movement']}\n\n## Transitions\n{e['transitions']}\n\n## Subtitles\nSee subtitles_en.srt (timed to the scene VO).\n\n## Export\n{e['export']}\n"
    f["asset-checklist.md"] = "# Asset checklist\n\n" + "".join(f"- [{'x' if a['status']=='done' else ' '}] **{a['item']}** — {a['status']}" + (f" — `{a['file']}`" if a['file'] != '—' else '') + (f" _{a.get('note','')}_" if a.get('note') else '') + "\n" for a in pkg["asset_checklist"])
    f["thumbnail-prompt.md"] = f"# Cover / thumbnail\n\n**Title:** {pkg['cover']['title']}\n\n**Design:** {pkg['cover']['design']}\n\n**Prompt / how to reproduce:** {pkg['cover']['prompt']}\n"
    f["subtitles_en.srt"] = srt(pkg)
    f["qc-report.md"] = qc.markdown(qc.run(pkg))
    return f


def folder_name(pkg):
    return pkg.get("slug") or f"trackmyrupee_{pkg['id'].replace('-', '_')}"


def write_folder(pkg):
    d = OUT / folder_name(pkg); d.mkdir(parents=True, exist_ok=True)
    for name, text in build_files(pkg).items():
        (d / name).write_text(text, "utf-8")
    return d


def zip_bytes(pkg):
    d = write_folder(pkg)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(d.rglob("*")):
            if p.is_file():
                z.write(p, f"{d.name}/{p.relative_to(d)}")
        for a in store.read("assets", []):
            if a["content_id"] == pkg["id"] and Path(a["path"]).exists():
                z.write(a["path"], f"{d.name}/uploads/{a['name']}")
    return buf.getvalue(), d.name
