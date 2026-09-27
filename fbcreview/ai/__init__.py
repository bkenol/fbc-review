"""The AI reader — a model that reads sheets and proposes located values.

`docs/ARCHITECTURE-V2.md` §3.3 and `CLAUDE.md` "AI reads; rules decide". Nothing
in `fbcreview/rules` or `fbcreview/codes` imports this package; a test walks the
import graph. `reader` makes the network calls and is only reached from the
worker (or `run.py --ai`); `grounding` and `readings` are pure and are what the
review consumes.
"""
