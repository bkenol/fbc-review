# fbcreview

Florida Building Code review of permit sets. PDF in, findings out. **AI reads; rules
decide:** the rules and the code corpus are pure Python, and when a deployment turns it on,
Claude also reads each sheet — every value it proposes is found on the sheet before a rule
may use it. Off, the review is deterministic and calls no model.

```bash
pip install -r requirements.txt

python run.py path/to/permit-set.pdf --json findings.json   # CLI
python run.py set.pdf --ai --save-readings readings.json     # also read sheets with Claude
python run.py set.pdf --readings readings.json               # replay a reading, no API call
uvicorn webapp.server:app --port 8000                        # web service -> http://127.0.0.1:8000
```

```
fbcreview/
  facts.py          typed project fact model — the contract between extraction and rules
  confidence.py     Evidence / Abstention — every value knows where it came from
  layout/           each sheet laid out once: CAD text lines, label/value pairs, table rows
  read/             the field catalog, value parsers and the deterministic reader
  factstore.py      every reading of every field, resolved — with rivals and provenance
  ai/               the optional AI sheet reader: prompt, cache, and the grounding check
  extract/
    document.py     sheet identification from the title block, CAD layer inventory
    scale.py        drawing scale from /Measure + printed labels, with abstention
    geometry.py     layer-filtered path extraction, measured distances
    blocks.py       unruled code-data blocks, keyed by cited section number
    schedules.py    ruled schedules via find_tables, plus merged-row/-header repair
  codes/fbc2023.py  structured code requirements — data, not literals in rules
  rules/            pure functions: ProjectFacts -> [Finding]. No I/O, no model.
  options.py        ReviewOptions — occupancy group, sprinklers, severity floor, delivery
  render/markup.py  general renderer: source PDF + findings -> reviewed PDF
  pipeline.py       build_facts() then run_all()
webapp/             FastAPI service + drag-and-drop front end (see webapp/README.md)
  feedback_schema.py  what a reviewer may say about a finding — data, not UI
  calibration.py      the levers feedback may move, applied after the corpus
  triage.py           where a report has to be fixed: a knob, code, or a person
  assist.py           the feedback-comment model call; never reaches a review
  notify.py           mail, the prompt export, and GitHub issues
tests/              regression against the hand-established findings
ARCHITECTURE.md     what mechanises, what does not, and what it costs
Dockerfile          python:3.12-slim, no GPU, ~2 s parse for a 24-sheet set
```

Design rules, in order of importance:

1. **Never guess.** A rule that cannot get its inputs records an `Abstention` with a reason.
   "Not checked" must never be indistinguishable from "checked and passed."
2. **Key on the most stable token.** Section citations, not labels. Layer names, not colours.
3. **Thresholds live in `codes/`, never in a rule.** A new code edition is a new data file.
4. **Every rule reports both ways.** Passing checks are findings too — they are what the
   verified register is made of.
5. **Calibration may re-level, never invent.** Training mode moves per-rule levers over a
   finished findings list; it cannot conjure a check, re-read a table or change a citation.
   Silencing a rule records an abstention, so rule 1 still holds. See
   `docs/TRAINING-MODE.md`.
6. **An abstention is classified, never re-written.** `webapp/abstentions.py` reads the
   reason a rule gave and says which class of failure it is — so "the set does not state
   this", where the rule was right, stops looking identical to "we could not read the part
   of the set that states it", which is a defect. It classifies the reason and never the
   drawing: it has not seen the sheet and never claims a value is printed on one.
7. **AI reads; rules decide.** A model's output can only become a claim about where a value
   is printed — never a finding, a severity, a threshold or a citation. A claim the grounding
   check cannot find on the sheet is discarded, and one it keeps says it was read by AI.
   `CLAUDE.md` has the full list; `tests/test_ai_guardrails.py` holds each item.
