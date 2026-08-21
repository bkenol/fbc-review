#!/usr/bin/env python3
"""Verify that rebuilding a scanned sheet recovers what the rules actually need.

Runs inside the container, where Tesseract lives:

    docker build -t fbc-review:dev .
    docker run --rm -v "$PWD/scripts:/app/scripts:ro" fbc-review:dev         python scripts/verify_ocr.py

Success is not "OCR produced some text". It is that the two things the
extraction strategy keys on survive the round trip: the printed scale label
(ARCHITECTURE.md section 3) and the cited section numbers (section 4).
"""
import warnings, tempfile, os; warnings.filterwarnings("ignore")
import pymupdf
from webapp import convert, pdfkind

SHEET = [
    "SHEET G-0   LIFE SAFETY PLAN",
    'SCALE: 1/4" = 1\'-0"',
    "COMMON PATH OF EGRESS TRAVEL 75 FT (1006.2.1)",
    "TRAVEL DISTANCE 250 FT (1017.2)",
    "OCCUPANT LOAD 70   RISK CATEGORY III",
]

# Build a page of real text, then FLATTEN it to an image -> a scanned sheet.
doc = pymupdf.open(); page = doc.new_page(width=1224, height=792)
y = 120
for line in SHEET:
    page.insert_text((90, y), line, fontsize=30); y += 70
for n in range(60):
    page.draw_line((80, 520 + n), (1140, 520 + n)) if n % 20 == 0 else None
flat = page.get_pixmap(dpi=200)
doc.close()

scan = pymupdf.open(); sp = scan.new_page(width=1224, height=792)
sp.insert_image(sp.rect, pixmap=flat)
src = os.path.join(tempfile.mkdtemp(), "scanned.pdf"); scan.save(src); scan.close()

before = pdfkind.profile(src)
print(f"BEFORE  kind={before.kind}  live_chars={before.sheets[0].live_chars}  "
      f"vector_items={before.sheets[0].vector_items}")

dest = src.replace("scanned", "rebuilt")
rep = convert.convert(src, before.raster_pages, dest)
print(f"REPORT  ocr={rep.ocr_used} vec={rep.vectorise_used} "
      f"chars={rep.total_chars} segments={rep.total_segments} secs={rep.seconds:.1f}")

after = pdfkind.profile(dest)
print(f"AFTER   kind={after.kind}  live_chars={after.sheets[0].live_chars}  "
      f"vector_items={after.sheets[0].vector_items}")

d = pymupdf.open(dest); text = d[0].get_text(); d.close()
print("\n--- RECOVERED TEXT ---")
print(text.strip()[:400])

print("\n--- DOES THE ENGINE SEE WHAT IT NEEDS? ---")
import re
from fbcreview.extract.scale import label_candidates
print("scale labels found :", label_candidates(text))
for sec in ("1006.2.1", "1017.2"):
    print(f"citation {sec:9} :", sec in text)
