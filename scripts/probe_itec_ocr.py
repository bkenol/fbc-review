#!/usr/bin/env python3
"""Does OCR recover ITEC's code-analysis block?

G-002 "CODE COMPLIANCE DATA" is 52% raster with ~2k live characters: the sheet
title is live text, the code table is a picture. That is why every code rule
abstains on this set — there is no cited section number to key on, because the
citations are pixels.

Run in the container, where Tesseract lives.
"""
import warnings, sys, tempfile, os, re
warnings.filterwarnings("ignore")
sys.path.insert(0, "/app")
import pymupdf
from webapp import convert, pdfkind

SRC = "/app/samples/ITEC - Building Plans.pdf"
PAGE = 1

src = pymupdf.open(SRC)
one = pymupdf.open()
one.insert_pdf(src, from_page=PAGE, to_page=PAGE)
tmp = tempfile.mkdtemp()
sheet = os.path.join(tmp, "g002.pdf")
one.save(sheet); one.close(); src.close()

before = pdfkind.profile(sheet)
s0 = before.sheets[0]
print(f"BEFORE  kind={s0.kind}  vector_items={s0.vector_items}  live_chars={s0.live_chars}  imgcov={s0.image_coverage}")
print(f"        document verdict: {before.kind}  -> raster_pages={before.raster_pages}")
print(f"        NOTE: page-level classifier does NOT flag this for rebuild.\n")

out = os.path.join(tmp, "g002-rebuilt.pdf")
rep = convert.convert(sheet, [0], out, do_ocr=True, do_vectorise=False)
print(f"REPORT  ocr={rep.ocr_used}  recovered_chars={rep.total_chars}  {rep.seconds:.1f}s\n")

d = pymupdf.open(out); text = d[0].get_text(); d.close()
up = text.upper()
print("--- RECOVERED TEXT (first 900 chars) ---")
print(" ".join(text.split())[:900])
print()
print("--- WHAT THE RULES NEED ---")
for t in ("OCCUPANT LOAD","TRAVEL DISTANCE","COMMON PATH","DEAD END","OCCUPANCY","SPRINKLER","CONSTRUCTION TYPE"):
    print(f"  {t:<18} {'FOUND' if t in up else '-'}")
cites = sorted(set(re.findall(r"\(?(\d{3,4}\.\d+(?:\.\d+)*)\)?", text)))
print(f"  section numbers    {cites[:12] if cites else 'none'}")
