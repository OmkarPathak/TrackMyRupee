"use strict";
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let S = null, IDEAS = [], CUR = { pk: null }, F = { q: "", pillar: "", format: "", status: "" };

async function api(url, opt = {}) {
  const o = { ...opt };
  if (o.json !== undefined) { o.method = o.method || "POST"; o.headers = { "Content-Type": "application/json" }; o.body = JSON.stringify(o.json); delete o.json; }
  const r = await fetch(url, o); let d = {};
  try { d = await r.json(); } catch (e) { }
  if (!r.ok) throw new Error(d.error || r.statusText);
  return d;
}
function toast(m, err) { const t = $("#toast"); t.textContent = m; t.className = "show" + (err ? " err" : ""); clearTimeout(t._h); t._h = setTimeout(() => t.className = "", 2800); }
const guard = fn => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };
const tag = p => `<span class="tag ${p}">${p} · ${esc(S.pillars[p])}</span>`;
const opts = (arr, sel) => arr.map(x => `<option ${x === sel ? "selected" : ""}>${esc(x)}</option>`).join("");

const MENU = [["dashboard", "Dashboard"], ["ideas", "Reel ideas"], ["script", "Reel script generator"], ["carousels", "Carousel generator"], ["prompts", "AI visual prompts"],
["calendar", "Content calendar"], ["assets", "Asset library"], ["production", "Production status"], ["performance", "Performance"], ["brand", "Brand settings"], ["export", "Export & download"]];

async function load() { S = await api("/api/state"); IDEAS = await api("/api/ideas"); }
async function route() {
  const [, page = "dashboard", arg] = location.hash.split("/");
  $("#menu").innerHTML = MENU.map(([k, l]) => `<a href="#/${k}" class="${k === page ? "on" : ""}">${l}</a>`).join("");
  $("#view").innerHTML = '<p class="muted">Loading…</p>';
  try { await load(); await (VIEWS[page] || VIEWS.dashboard)(arg); } catch (e) { $("#view").innerHTML = `<div class="card fail">${esc(e.message)}</div>`; }
}
window.addEventListener("hashchange", route); route();

const v = h => $("#view").innerHTML = h;
const TARGET = { A: 30, B: 25, C: 20, D: 15, E: 10 };

const VIEWS = {
  async dashboard() {
    const st = S.stats, tot = Object.values(st.by_pillar).reduce((a, b) => a + b, 0) || 1;
    v(`<h1>Dashboard</h1><div class="grid">${[["Content ideas", st.ideas], ["Scripts generated", st.scripts], ["Reels completed", st.reels_completed], ["Carousels completed", st.carousels_completed], ["Pending assets", st.pending_assets], ["Published", st.published]].map(([l, n]) => `<div class="card stat"><b>${n}</b><span>${l}</span></div>`).join("")}</div>
    <div class="card"><h3>Content by pillar <span class="muted" style="font-size:13px">(actual vs target mix)</span></h3>${Object.entries(st.by_pillar).map(([p, n]) => `<div class="row" style="margin:6px 0"><div style="width:230px">${tag(p)}</div><div class="bar" style="flex:1"><i style="width:${100 * n / tot}%"></i></div><span style="width:120px">${n} (${Math.round(100 * n / tot)}% · target ${TARGET[p]}%)</span></div>`).join("")}</div>
    <div class="card"><h3>Workflow</h3><p class="muted">One piece at a time: pick an idea → get its package → review/edit → approve → export. Nothing advances without your approval.</p>
    <div class="row">${IDEAS.filter(i => S.packages.includes(i.id)).map(i => `<a class="btn pri" href="#/script/${i.id}">Open: ${esc(i.title)} (${esc(i.status)})</a>`).join("") || '<span class="muted">No developed packages yet.</span>'}</div></div>
    <div class="card"><h3>Integrations</h3>${S.providers.map(p => `<div>${p.available ? "✅" : "⚪"} <b>${esc(p.name)}</b> <span class="muted">(${p.kinds.join(", ")}) — ${p.available ? "key found" : "needs " + p.env.join(", ") + " — manual upload + copy-ready prompts are used instead"}</span></div>`).join("")}</div>`);
  },

  async ideas() { await listView("reel", "Reel ideas"); },
  async carousels() {
    await listView("carousel", "Carousel generator", `<p class="muted">Carousels are authored one at a time (Phase 5 of the workflow). Pick one and request it; the renderer (<code>render/carousel.py</code>) outputs 1080×1350 PNGs + PDF.</p>`);
  },

  async script(id) {
    if (!id) { const d = S.packages[0]; if (d) { location.hash = "#/script/" + d; return; } v(`<h1>Reel script generator</h1><div class="empty">No developed packages yet. Use Reel ideas → Generate.</div>`); return; }
    const r = await api("/api/package/" + id); CUR = { id, ...r };
    if (!r.package) return v(`<h1>${esc(r.idea.title)}</h1><div class="empty">No package yet.<br><br><button class="pri" data-act="gen" data-id="${id}">Generate content</button></div>`);
    drawScript(CUR.tab || "overview");
  },

  async prompts() {
    const pk = S.packages; if (!pk.length) return v(`<h1>AI visual prompts</h1><div class="empty">Prompts appear once a package is developed.</div>`);
    let h = `<h1>AI visual prompts</h1><p class="muted">Copy-ready prompts. If no image provider key is set, paste these into your tool of choice and upload results in the Asset library. Real app UI must come from real screenshots.</p>`;
    for (const id of pk) {
      const p = (await api("/api/package/" + id)).package;
      h += `<div class="card"><h2>${esc(p.title)}</h2><h3>Character bible</h3><pre>${esc(p.character.bible)}</pre><button data-act="copy" data-t="${esc(p.character.bible)}">Copy</button>` +
        p.image_prompts.map(x => `<div class="scene"><b>${esc(x.id)}</b> — ${esc(x.use)}<pre>${esc(x.prompt)}</pre><div class="muted">Negative: ${esc(x.negative)}</div><button data-act="copy" data-t="${esc(x.prompt + "\n\nNegative: " + x.negative)}">Copy prompt</button></div>`).join("") +
        `<h3>Image-to-video</h3>` + p.video_prompts.map(x => `<div class="scene"><b>${esc(x.id)}</b> (from ${esc(x.from)})<pre>${esc(x.prompt)}</pre><button data-act="copy" data-t="${esc(x.prompt)}">Copy</button></div>`).join("") + `</div>`;
    }
    v(h);
  },

  async calendar() {
    const cal = await api("/api/calendar"), ctas = await api("/api/lib/ctas");
    v(`<h1>30-day content calendar</h1><p class="muted">One Reel daily, three carousels a week, Stories supporting each. Dates are editable (start ${esc(cal[0].date)}).</p><div class="card" style="overflow:auto"><table><tr><th>Day</th><th>Date</th><th>Content</th><th>Pillar</th><th>Format</th><th>Objective</th><th>CTA</th><th>Assets</th><th>Status</th></tr>
    ${cal.map(c => `<tr><td>${c.day}</td><td><input type="date" value="${esc(c.date)}" data-cal="${c.day}/${c.format}" data-f="date"></td><td>${esc(c.title)}<div class="muted" style="font-size:12px">${esc(c.story)}</div></td><td>${tag(c.pillar)}</td><td>${c.format}</td><td>${esc(c.objective)}</td>
    <td><select data-cal="${c.day}/${c.format}" data-f="cta" style="max-width:190px">${opts([...new Set([c.cta, ...ctas])], c.cta)}</select></td><td class="muted">${esc(c.assets)}</td>
    <td><select data-cal="${c.day}/${c.format}" data-f="status">${opts(S.statuses, c.status)}</select></td></tr>`).join("")}</table></div>`);
  },

  async assets() {
    const a = await api("/api/assets");
    const gen = [];
    for (const id of S.packages) { const r = await api("/api/package/" + id); r.files.forEach(f => gen.push({ slug: r.slug, f })); }
    v(`<h1>Asset library</h1><div class="card"><h3>Upload</h3><form id="up" class="row"><select name="content_id">${IDEAS.map(i => `<option value="${i.id}">${esc(i.id)} — ${esc(i.title.slice(0, 40))}</option>`).join("")}</select><input name="label" placeholder="label (e.g. scene 3 image)"><input type="file" name="file" required><button class="pri">Upload</button></form><p class="muted">png, jpg, webp, mp4, mov, mp3, wav, m4a, srt, pdf. Uploads are included in the ZIP export.</p></div>
    <div class="card"><h3>Uploaded (${a.length})</h3>${a.length ? `<table>${a.map(x => `<tr><td><a href="${esc(x.url)}" target="_blank">${esc(x.name)}</a></td><td>${esc(x.content_id)}</td><td>${esc(x.label)}</td><td>${(x.size / 1024).toFixed(0)} KB</td><td><button class="dng" data-act="delasset" data-id="${x.id}">Delete</button></td></tr>`).join("")}</table>` : '<div class="empty">Nothing uploaded yet.</div>'}</div>
    <div class="card"><h3>Generated by the studio</h3>${gen.length ? gen.map(g => `<div><a href="/files/${g.slug}/${g.f}" target="_blank">${esc(g.slug)}/${esc(g.f)}</a></div>`).join("") : '<div class="empty">No generated files.</div>'}</div>`);
  },

  async production() {
    v(`<h1>Production status</h1><div class="kan">${S.statuses.map(s => `<div class="col"><b>${s}</b> <span class="muted">(${IDEAS.filter(i => i.status === s).length})</span>${IDEAS.filter(i => i.status === s && (s !== "Idea" || S.packages.includes(i.id) || false)).map(i => `<div class="card" style="margin:8px 0;padding:10px"><a href="#/script/${i.id}">${esc(i.title)}</a><br><select data-status="${i.id}">${opts(S.statuses, i.status)}</select></div>`).join("")}${s === "Idea" ? `<div class="muted" style="padding:8px">${IDEAS.filter(i => i.status === "Idea").length} ideas in the library (see Reel ideas)</div>` : ""}</div>`).join("")}</div>`);
  },

  async performance() {
    const m = await api("/api/metrics"), f = k => (n) => `<td>${n}</td>`;
    const pub = IDEAS.filter(i => ["Published", "Approved", "Review"].includes(i.status));
    v(`<h1>Performance</h1><p class="muted">Numbers you enter by hand from Instagram Insights — no Instagram integration is used. CTR = link clicks ÷ reach; conversion = registrations ÷ link clicks.</p>
    <div class="card"><h3>Add metrics</h3><form id="mf" class="row"><select name="content_id">${IDEAS.map(i => `<option value="${i.id}">${esc(i.id)} — ${esc(i.title.slice(0, 40))}</option>`).join("")}</select><input type="date" name="published_date"><select name="hook_style"><option value="">hook style…</option>${opts(S.hook_styles)}</select>
    ${["views", "reach", "likes", "comments", "shares", "saves", "avg_watch_s", "profile_visits", "link_clicks", "registrations"].map(k => `<input name="${k}" type="number" min="0" step="any" placeholder="${k.replace(/_/g, " ")}" style="width:110px">`).join("")}<button class="pri">Save</button></form></div>
    <div class="card"><h3>Suggestions</h3><ul>${m.suggestions.map(s => `<li>${esc(s)}</li>`).join("")}</ul></div>
    <div class="card" style="overflow:auto"><h3>Entries</h3>${m.entries.length ? `<table><tr><th>Content</th><th>Reach</th><th>Eng.</th><th>Share</th><th>Save</th><th>Profile</th><th>CTR</th><th>Conv.</th><th></th></tr>${m.entries.map(e => `<tr><td>${esc(e.content_id)}</td><td>${e.reach}</td><td>${e.rates.engagement_rate}%</td><td>${e.rates.share_rate}%</td><td>${e.rates.save_rate}%</td><td>${e.rates.profile_visit_rate}%</td><td>${e.rates.ctr}%</td><td>${e.rates.conversion_rate}%</td><td><button class="dng" data-act="delm" data-id="${e.id}">×</button></td></tr>`).join("")}</table>` : '<div class="empty">No metrics yet.</div>'}</div>
    ${Object.entries(m.compare).map(([d, rows]) => rows.length ? `<div class="card" style="overflow:auto"><h3>By ${d.replace("_", " ")}</h3><table><tr><th>${d}</th><th>Posts</th><th>Eng.</th><th>Share</th><th>Save</th><th>Profile</th><th>CTR</th><th>Conv.</th></tr>${rows.map(r => `<tr><td>${esc(r.key)}</td><td>${r.posts}</td><td>${r.engagement_rate}%</td><td>${r.share_rate}%</td><td>${r.save_rate}%</td><td>${r.profile_visit_rate}%</td><td>${r.ctr}%</td><td>${r.conversion_rate}%</td></tr>`).join("")}</table></div>` : "").join("")}`);
  },

  async brand() {
    const b = await api("/api/lib/brand"), hooks = await api("/api/lib/hooks"), ctas = await api("/api/lib/ctas"), series = await api("/api/lib/series"), ct = await api("/api/lib/caption_templates"), ht = await api("/api/lib/hashtag_templates");
    v(`<h1>Brand settings</h1><div class="card"><h3>Colours (from trackmyrupee.com tokens)</h3><div class="row">${Object.entries(b.colors).map(([k, c]) => `<div style="text-align:center"><span class="sw" style="background:${c}"></span><div style="font-size:11px">${k}<br>${c}</div></div>`).join("")}</div>
    <p>Fonts: <b>${esc(b.fonts.display)}</b> (display) · <b>${esc(b.fonts.ui)}</b> (UI) · ${esc(b.fonts.hindi)} (Hindi). Safe zone: ${esc(b.safe_zone)}</p>${b.approved_style ? `<h3>Approved creative style</h3><pre>${esc(JSON.stringify(b.approved_style, null, 1))}</pre>` : '<p class="muted">No approved style recorded yet — it is recorded when you approve the first Reel.</p>'}</div>
    <div class="two"><div class="card"><h3>✅ Verified claims we can use</h3><ul>${b.verified_claims.map(c => `<li>${esc(c)}</li>`).join("")}</ul></div><div class="card"><h3>🚫 Never claim</h3><ul>${b.never_claim.map(c => `<li>${esc(c)}</li>`).join("")}</ul></div></div>
    <div class="card"><h3>Edit brand JSON</h3><textarea id="bj" style="min-height:200px;font-family:monospace">${esc(JSON.stringify(b, null, 1))}</textarea><button class="pri" data-act="savelib" data-name="brand" data-src="bj">Save brand</button></div>
    <div class="card"><h3>CTA bank (${ctas.length})</h3><textarea id="cj" style="min-height:140px">${esc(ctas.join("\n"))}</textarea><button class="pri" data-act="savectas">Save CTAs (one per line)</button></div>
    <div class="card"><h3>Hook bank (${hooks.length})</h3><textarea id="hj" style="min-height:160px">${esc(hooks.map(h => h.pillar + " | " + h.text).join("\n"))}</textarea><button class="pri" data-act="savehooks">Save hooks (PILLAR | text)</button></div>
    <div class="card"><h3>Caption & hashtag templates</h3><textarea id="tj" style="min-height:140px;font-family:monospace">${esc(JSON.stringify({ caption_templates: ct, hashtag_templates: ht }, null, 1))}</textarea><button class="pri" data-act="savetpl">Save templates</button></div>
    <div class="card"><h3>Content series (${series.length})</h3>${series.map(s => `<details><summary><b>${esc(s.name)}</b> — ${esc(s.tagline)}</summary><p><b>Intro:</b> ${esc(s.intro)}<br><b>Outro:</b> ${esc(s.outro)}<br><b>Thumbnail:</b> ${esc(s.thumbnail)}<br><b>Colour:</b> ${esc(s.colour)}<br><b>Numbering:</b> ${esc(s.numbering)}</p><ol>${s.episodes.map(e => `<li>${esc(e)}</li>`).join("")}</ol></details>`).join("")}</div>`);
  },

  async export() {
    v(`<h1>Export & download</h1>${S.packages.length ? S.packages.map(id => { const i = IDEAS.find(x => x.id === id); return `<div class="card row sp"><div><b>${esc(i.title)}</b><br><span class="muted">Production folder + uploads as ZIP (script, storyboard, voiceover, prompts, caption, hashtags, editing, checklist, QC, SRT, cover, video if rendered)</span></div><a class="btn pri" href="/api/export/${id}.zip">Download ZIP</a></div>`; }).join("") : '<div class="empty">No developed packages to export yet.</div>'}`);
  },
};

async function listView(kind, title, note = "") {
  const rows = IDEAS.filter(i => i.kind === kind).filter(i => (!F.pillar || i.pillar === F.pillar) && (!F.format || i.format === F.format) && (!F.status || i.status === F.status) && (!F.q || (i.title + i.concept).toLowerCase().includes(F.q.toLowerCase())));
  v(`<div class="row sp"><h1>${title}</h1><button class="pri" data-act="newidea" data-kind="${kind}">+ New idea</button></div>${note}
  <div class="row card"><input id="fq" placeholder="Search…" value="${esc(F.q)}"><select id="fp"><option value="">All pillars</option>${Object.entries(S.pillars).map(([k, n]) => `<option value="${k}" ${F.pillar === k ? "selected" : ""}>${k} · ${esc(n)}</option>`).join("")}</select>
  <select id="ff"><option value="">All formats</option>${opts([...new Set(IDEAS.filter(i => i.kind === kind).map(i => i.format))], F.format)}</select><select id="fs"><option value="">All statuses</option>${opts(S.statuses, F.status)}</select><span class="muted">${rows.length} shown</span></div>
  ${rows.length ? rows.map(i => `<div class="card"><div class="row sp"><div><b>${i.no}. ${esc(i.title)}</b> ${tag(i.pillar)} <span class="tag" style="background:var(--bg);color:var(--ink2)">${esc(i.format)}${i.duration ? " · " + esc(i.duration) : ""}${i.slides ? " · " + i.slides + " slides" : ""}</span></div><select data-status="${i.id}">${opts(S.statuses, i.status)}</select></div>
  <div class="muted" style="margin:6px 0">${esc(i.concept || (i.reel_link ? "Linked to Reel " + i.reel_link : ""))}</div>
  <div class="row"><button class="pri" data-act="${S.packages.includes(i.id) ? "open" : "gen"}" data-id="${i.id}">${S.packages.includes(i.id) ? "Open package" : "Generate content"}</button><button data-act="edit" data-id="${i.id}">Edit</button><button data-act="dup" data-id="${i.id}">Duplicate</button><button class="dng" data-act="del" data-id="${i.id}">Delete</button></div></div>`).join("") : '<div class="empty">No ideas match your filters.</div>'}`);
  $("#fq").oninput = e => { F.q = e.target.value; clearTimeout(window._ft); window._ft = setTimeout(() => listView(kind, title, note).then(() => { const q = $("#fq"); q.focus(); q.setSelectionRange(99, 99); }), 250); };
  $("#fp").onchange = e => { F.pillar = e.target.value; listView(kind, title, note); }; $("#ff").onchange = e => { F.format = e.target.value; listView(kind, title, note); }; $("#fs").onchange = e => { F.status = e.target.value; listView(kind, title, note); };
}

const TABS = [["overview", "Strategy"], ["hooks", "Hooks"], ["script", "Script & VO"], ["storyboard", "Storyboard"], ["editing", "Editing"], ["publish", "Publishing"], ["preview", "Preview"], ["qc", "QC"], ["approve", "Approval"]];
function drawScript(tab) {
  CUR.tab = tab; const p = CUR.package, i = CUR.idea, sum = CUR.qc_summary;
  let b = "";
  if (tab === "overview") b = `<div class="card"><pre>${esc(Object.entries(p.strategy).map(([k, x]) => k.replace(/_/g, " ").toUpperCase() + ": " + x).join("\n\n"))}</pre></div><div class="card"><h3>Illustrative budget</h3><p class="muted">${esc(p.illustrative_budget.label)}</p><table>${p.illustrative_budget.groups.map(g => `<tr><td>${esc(g.name)}</td><td>₹${g.amount.toLocaleString("en-IN")}</td></tr>`).join("")}</table></div><div class="card"><h3>Character</h3><p class="muted">${esc(p.character.note)}</p><pre>${esc(p.character.name + " — " + p.character.bible)}</pre></div>`;
  if (tab === "hooks") b = `<div class="card"><h3>Hook options</h3>${p.hooks.options.map((h, n) => `<div class="row" style="margin:6px 0"><span>${n === p.hooks.selected ? "⭐" : n + 1 + "."}</span><input data-hook="${n}" value="${esc(h)}" style="flex:1"><button data-act="selhook" data-n="${n}">Select</button></div>`).join("")}<p class="muted">${esc(p.hooks.note || "")}</p><button class="pri" data-act="savepk">Save edits</button></div><div class="card"><h3>More hooks</h3><p class="muted">Draws from the ${"50+"} hook bank for this pillar. For bespoke rewrites, ask Claude.</p><button data-act="regen" data-w="hooks">Regenerate hooks</button>${(p.hooks.alternatives || []).map(h => `<div class="row" style="margin:6px 0">• ${esc(h)} <button data-act="copy" data-t="${esc(h)}">Copy</button></div>`).join("")}</div>`;
  if (tab === "script") b = `<div class="card"><label class="f">Voiceover direction</label><textarea data-vod>${esc(p.voiceover.direction)}</textarea><p class="muted">${esc(p.voiceover.tts_status)}</p></div>` + p.scenes.map((s, n) => `<div class="card scene"><b>Scene ${s.n} · ${esc(s.t)}s · ${esc(s.name)}</b><label class="f">On-screen text (reference; render timing is fixed in the renderer)</label><textarea data-s="${n}" data-k="on_screen">${esc(s.on_screen)}</textarea><div class="two"><div><label class="f">VO English</label><textarea data-s="${n}" data-k="vo_en">${esc(s.vo_en)}</textarea></div><div><label class="f">VO Hinglish</label><textarea data-s="${n}" data-k="vo_hinglish">${esc(s.vo_hinglish)}</textarea></div></div><label class="f">VO Hindi</label><textarea data-s="${n}" data-k="vo_hi">${esc(s.vo_hi)}</textarea><div class="muted">SFX: ${esc(s.sfx)}</div></div>`).join("") + `<div class="card"><h3>Music</h3><pre>${esc(p.music.direction + "\n\n" + p.music.structure + "\n\n" + p.music.licensing)}</pre></div><button class="pri" data-act="savepk">Save edits</button><p class="muted">Note: edited text updates scripts/exports. On-screen text in the rendered video changes only after a renderer edit (ask Claude for revisions).</p>`;
  if (tab === "storyboard") b = p.scenes.map(s => `<div class="card scene"><b>Scene ${s.n} · ${esc(s.t)}s · ${esc(s.name)}</b><table>${["visual", "camera", "background", "lighting", "movement", "on_screen", "vo_en", "transition"].map(k => `<tr><th style="width:120px">${k.replace("_", " ")}</th><td>${esc(s[k])}</td></tr>`).join("")}</table></div>`).join("");
  if (tab === "editing") b = `<div class="card"><h3>CapCut editing</h3><p><b>Tool:</b> ${esc(p.editing.tool)}<br><b>Canvas:</b> ${esc(p.editing.canvas)}</p><ul>${p.editing.timeline.map(t => `<li>${esc(t)}</li>`).join("")}</ul><p><b>Text placement:</b> ${esc(p.editing.text_placement)}</p><p><b>Transitions:</b> ${esc(p.editing.transitions)}</p><p><b>Export:</b> ${esc(p.editing.export)}</p></div><div class="card"><h3>Asset checklist</h3>${p.asset_checklist.map(a => `<div>${a.status === "done" ? "✅" : a.status === "optional" ? "◻️" : "⏳"} ${esc(a.item)} <span class="muted">${esc(a.note || a.file)}</span></div>`).join("")}</div>`;
  if (tab === "publish") { const pub = p.publishing; b = `<div class="card"><label class="f">Caption</label><textarea data-pub="caption" style="min-height:260px">${esc(pub.caption)}</textarea><label class="f">Pinned comment</label><textarea data-pub="pinned_comment">${esc(pub.pinned_comment)}</textarea><div class="row"><label class="f">CTA</label><input id="ctain" value="${esc(pub.cta)}" style="flex:1"><button data-act="setcta">Change CTA</button><button data-act="ctapick">Pick from bank</button></div><div class="row" style="margin-top:8px"><button class="pri" data-act="savepk">Save edits</button><button data-act="regen" data-w="caption">Regenerate caption (new CTA + hashtag order)</button><button data-act="copy" data-t="${esc(pub.caption + "\n\n" + pub.hashtags_core.join(" "))}">Copy caption + tags</button></div></div><div class="card"><b>Core hashtags:</b> ${esc(pub.hashtags_core.join(" "))}<br><b>Extended:</b> ${esc(pub.hashtags_extended.join(" "))}<p class="muted">${esc(pub.hashtag_note)}</p><b>Stories</b><ul>${pub.story_text.map(s => `<li>${esc(s)}</li>`).join("")}</ul></div>`; }
  if (tab === "preview") { const slug = CUR.slug, has = f => CUR.files.includes(f); b = `<div class="card"><div class="row sp"><h3>Preview</h3><span><button data-act="render" ${CUR.id !== "reel-001" ? "disabled" : ""}>Re-render video</button> <span id="rs" class="muted"></span></span></div><div class="row" style="align-items:flex-start">${CUR.files.filter(f => f.endsWith(".mp4") && f.includes("draft")).map(f => `<div><video controls playsinline src="/files/${slug}/${f}" height="560"></video><div class="muted">${esc(f.replace("trackmyrupee_reel_001_", ""))}</div></div>`).join("") || '<div class="empty">Video not rendered yet.</div>'}${has("cover_1080x1920.png") ? `<img class="cov" src="/files/${slug}/cover_1080x1920.png" height="620" alt="cover"> ` : ""}${has("cover_grid_crop_1080x1350.png") ? `<img class="cov" src="/files/${slug}/cover_grid_crop_1080x1350.png" height="400" alt="grid crop">` : ""}</div><p class="muted">Drafts: motion graphics + mascot + SFX; vo_* versions add the Kokoro-generated voiceover (en, en_hf = Hindi-voice speaking English, hi). No music yet.</p></div>`; }
  if (tab === "qc") b = `<div class="card"><h3>Quality control</h3>${CUR.qc.map(r => `<div class="${r.status}">${r.status === "pass" ? "✅" : r.status === "warn" ? "⚠️" : "❌"} <b>${esc(r.check)}</b> <span class="muted">${esc(r.detail)}</span></div>`).join("")}</div>`;
  if (tab === "approve") b = `<div class="card"><h3>Director approval</h3><p>Status: <b>${esc(i.status)}</b>. Requesting changes is <b>not</b> approval. Only click when you are happy; this records the approved style as the default reference for future content.</p><label class="f">Creative notes to remember (one per line: style, typography, hook style, VO tone, duration, editing…)</label><textarea id="an" style="min-height:140px">${esc((p.learning_log.notes || []).join("\n"))}</textarea><button class="pri" data-act="approve" ${p.learning_log.approved ? "disabled" : ""}>${p.learning_log.approved ? "Approved ✓" : "I approve this piece"}</button></div>`;
  v(`<div class="row sp"><div><h1>${esc(p.title)}</h1><span class="tag">${esc(i.status)}</span> ${tag(i.pillar)} <span class="muted">QC: ${sum.pass} pass · ${sum.warn} warn · ${sum.fail} fail</span></div><div class="row"><select data-status="${i.id}">${opts(S.statuses, i.status)}</select><a class="btn" href="/api/export/${i.id}.zip">Download ZIP</a></div></div><div class="tabs">${TABS.map(([k, l]) => `<button class="${k === tab ? "on" : ""}" data-act="tab" data-t="${k}">${l}</button>`).join("")}</div>${b}`);
}

function gatherEdits() {
  const p = CUR.package;
  document.querySelectorAll("[data-hook]").forEach(e => p.hooks.options[+e.dataset.hook] = e.value);
  document.querySelectorAll("[data-s]").forEach(e => p.scenes[+e.dataset.s][e.dataset.k] = e.value);
  document.querySelectorAll("[data-pub]").forEach(e => p.publishing[e.dataset.pub] = e.value);
  const vo = $("[data-vod]"); if (vo) p.voiceover.direction = vo.value;
  p.voiceover.en = p.scenes.map(s => s.vo_en).join("\n\n"); p.voiceover.hinglish = p.scenes.map(s => s.vo_hinglish).join("\n\n"); p.voiceover.hindi = p.scenes.map(s => s.vo_hi).join("\n\n");
}
async function savePk() { gatherEdits(); const r = await api("/api/package/" + CUR.id, { method: "PUT", json: CUR.package }); Object.assign(CUR, r); toast("Saved"); drawScript(CUR.tab); }

document.addEventListener("click", guard(async e => {
  const el = e.target.closest("[data-act]"); if (!el) return; const a = el.dataset.act, id = el.dataset.id;
  if (a === "tab") { gatherEdits(); drawScript(el.dataset.t); }
  else if (a === "savepk") await savePk();
  else if (a === "copy") { await navigator.clipboard.writeText(el.dataset.t); toast("Copied"); }
  else if (a === "open") location.hash = "#/script/" + id;
  else if (a === "gen") { const r = await api("/api/generate/" + id, { method: "POST" }); if (r.status === "exists") location.hash = "#/script/" + id; else { prompt(r.message + "\n\nCopy this request:", r.request); } }
  else if (a === "dup") { await api(`/api/ideas/${id}/duplicate`, { method: "POST" }); toast("Duplicated"); route(); }
  else if (a === "del") { if (confirm("Delete this idea?")) { await api("/api/ideas/" + id, { method: "DELETE" }); route(); } }
  else if (a === "edit") { const i = IDEAS.find(x => x.id === id); const t = prompt("Title", i.title); if (t === null) return; const c = prompt("Concept / brief", i.concept || ""); await api("/api/ideas/" + id, { method: "PATCH", json: { title: t, concept: c ?? i.concept } }); route(); }
  else if (a === "newidea") { const title = prompt("Idea title"); if (!title) return; const pillar = (prompt("Pillar (A-E)", "A") || "").toUpperCase(); await api("/api/ideas", { json: { title, pillar, kind: el.dataset.kind } }); route(); }
  else if (a === "selhook") { gatherEdits(); CUR.package.hooks.selected = +el.dataset.n; await savePk(); }
  else if (a === "regen") { gatherEdits(); const r = await api(`/api/package/${CUR.id}/regen/${el.dataset.w}`, { json: {} }); CUR.package = r.package; toast("Regenerated"); drawScript(CUR.tab); }
  else if (a === "setcta") { gatherEdits(); const r = await api(`/api/package/${CUR.id}/regen/cta`, { json: { cta: $("#ctain").value } }); CUR.package = r.package; toast("CTA updated"); drawScript("publish"); }
  else if (a === "ctapick") { const ctas = await api("/api/lib/ctas"); const n = prompt(ctas.map((c, k) => `${k + 1}. ${c}`).join("\n") + "\n\nNumber:"); if (n && ctas[n - 1]) { $("#ctain").value = ctas[n - 1]; } }
  else if (a === "approve") { const notes = $("#an").value.split("\n").map(s => s.trim()).filter(Boolean); if (!confirm("Approve this piece? This records your creative preferences.")) return; await api(`/api/package/${CUR.id}/approve`, { json: { confirm: "APPROVED", notes, style: { source: CUR.id, notes } } }); toast("Approved & recorded"); VIEWS.script(CUR.id); }
  else if (a === "render") { await api("/api/render/" + CUR.id, { method: "POST" }); const t = setInterval(async () => { const j = await api("/api/render/" + CUR.id); const rs = $("#rs"); if (rs) rs.textContent = j.state; if (j.state !== "running") { clearInterval(t); toast("Render " + j.state, j.state === "error"); if (j.state === "done") VIEWS.script(CUR.id); } }, 4000); toast("Rendering (~3 min)…"); }
  else if (a === "delasset") { await api("/api/assets/" + id, { method: "DELETE" }); route(); }
  else if (a === "delm") { await api("/api/metrics/" + id, { method: "DELETE" }); route(); }
  else if (a === "savelib") { await api("/api/lib/" + el.dataset.name, { method: "PUT", json: JSON.parse($("#" + el.dataset.src).value) }); toast("Saved"); }
  else if (a === "savectas") { await api("/api/lib/ctas", { method: "PUT", json: $("#cj").value.split("\n").map(s => s.trim()).filter(Boolean) }); toast("Saved"); }
  else if (a === "savehooks") { const h = $("#hj").value.split("\n").map(s => s.trim()).filter(Boolean).map(l => { const [p, ...t] = l.split("|"); if (!"ABCDE".includes(p.trim()) || !t.length) throw new Error("Bad line: " + l); return { pillar: p.trim(), text: t.join("|").trim() }; }); await api("/api/lib/hooks", { method: "PUT", json: h }); toast("Saved"); }
  else if (a === "savetpl") { const o = JSON.parse($("#tj").value); await api("/api/lib/caption_templates", { method: "PUT", json: o.caption_templates }); await api("/api/lib/hashtag_templates", { method: "PUT", json: o.hashtag_templates }); toast("Saved"); }
}));
document.addEventListener("change", guard(async e => {
  const t = e.target;
  if (t.dataset.status) { await api("/api/ideas/" + t.dataset.status, { method: "PATCH", json: { status: t.value } }); toast("Status: " + t.value); if (CUR.id === t.dataset.status && location.hash.includes("script")) VIEWS.script(CUR.id); }
  else if (t.dataset.cal) { const [d, f] = t.dataset.cal.split("/"); await api(`/api/calendar/${d}/${f}`, { method: "PATCH", json: { [t.dataset.f]: t.value } }); toast("Saved"); }
}));
document.addEventListener("submit", guard(async e => {
  e.preventDefault(); const f = e.target;
  if (f.id === "up") { await api("/api/assets", { method: "POST", body: new FormData(f) }); toast("Uploaded"); route(); }
  if (f.id === "mf") { const d = Object.fromEntries(new FormData(f)); await api("/api/metrics", { json: d }); toast("Saved"); route(); }
}));
