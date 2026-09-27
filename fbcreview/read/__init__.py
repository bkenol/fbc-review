"""Readers: turning a laid-out sheet into claims about the building.

`catalog` is the vocabulary (data), `parse` reads values, `deterministic` is the
reader that needs no model. The AI reader lives in `fbcreview.ai` and writes into
the same `fbcreview.factstore.FactStore` through the grounding verifier.
"""
from .catalog import BY_KEY, DECLARATION_FIELDS, FIELDS, FieldSpec
from .deterministic import normalise_label, read_layouts

__all__ = ["BY_KEY", "DECLARATION_FIELDS", "FIELDS", "FieldSpec", "normalise_label",
           "read_layouts"]
