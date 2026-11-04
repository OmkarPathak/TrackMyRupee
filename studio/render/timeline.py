"""Single source of truth for Reel 001 timing (renderer, SFX, VO mixer, captions)."""
# scene windows (start, end) — see packages/reel_001.py for the matching scene text
SCENES = [(0.0, 3.4), (3.4, 6.0), (6.0, 9.0), (9.0, 12.4), (12.4, 15.0), (15.0, 19.1), (19.1, 23.1),
          (23.1, 30.3), (30.3, 37.3), (37.3, 43.3), (43.3, 47.5)]
DUR = 47.5
VO_OFFSET = 0.1
STARTS = [a + VO_OFFSET for a, _ in SCENES]
ENDS = [b for _, b in SCENES]

# tile story
GROUP_START = {"rent": 6.1, "emi": 7.0, "bills": 7.8, "food": 9.1, "travel": 10.3, "subs": 11.2, "sip": 12.5}
STORY_IN, STORY_OUT = 3.5, 23.1
REVEAL = 15.1           # mystery tiles turn coral
LEAKS = 19.3
HABIT = 23.3
# real-app scene
TYPE0, TYPE_STEP = 31.6, 0.12      # 10 typed characters
PARSED = 33.5                       # Enter pressed -> form filled
ADD_TAP = 35.7                      # Add pressed (coin into the piggy)
LIST_IN, REMINDER_IN = 37.5, 40.4
END_IN = 43.3
