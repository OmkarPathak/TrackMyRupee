# Studio workflow rules
- **Branch:** all Reel/carousel/studio code is committed and pushed to **`claude/modest-goodall-u3ocs5`** every time. Do not create new branches for reel work.
- **Performance tracker stays local:** metrics live in `studio/data/metrics.json` (the whole `studio/data/` folder is git-ignored). Never commit it, and never include it in exports. Check with `git check-ignore -v studio/data/metrics.json`.
- **Updating stats after each post:** Performance tab (or ask Claude with the numbers). Blank fields stay blank (“—”); reach, saves, registrations and publish date are optional but make the rates more meaningful.
- **Approval first:** one content piece at a time; nothing is marked approved until the creative director says so (see STYLE_GUIDE.md).
