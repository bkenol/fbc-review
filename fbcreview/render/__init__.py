"""Rendering.

`v5_markup_reference.py` is the renderer exactly as it produced the delivered
34-page review: mediabox extension, per-sheet rail, adaptive legend, on-drawing
markers with live annotations, and the multi-page register section.

`v5_register_reference.py` is the hand-authored finding register it consumed —
the specification the rule engine is being written to reproduce. Every entry in
it is a rule that either exists in `fbcreview/rules/` or is still to be written;
diffing the two is the coverage backlog.

Step 6 in ARCHITECTURE.md is to feed the renderer `Finding` objects from the
rule engine instead of that hand-authored list, at which point PDF-in/PDF-out is
fully mechanical.
"""
