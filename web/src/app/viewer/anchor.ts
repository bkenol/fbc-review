/**
 * Finding an engine anchor on a rendered page.
 *
 * A `Finding` says which sheet it is about and carries an `anchor` — the text
 * to box on the drawing — and a `hit`, the index of the occurrence it means,
 * and no coordinates. The engine's renderer resolves those two into a position
 * when it draws the marked-up PDF, with `page.search_for(anchor)[hit]`. The
 * viewer has to do the same search to put the same marker in the same place.
 *
 * Separated from the component because it is the part most likely to be wrong
 * and the part easiest to test: string handling against a text layer, with no
 * canvas, no pdf.js and no DOM.
 */

export interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/** One run of text off the page, already reduced to a box. */
export interface AnchorItem {
  text: string;
  box: Box;
}

/**
 * How much of a long anchor has to match when the whole of it does not.
 *
 * Sheets abbreviate: a code data block that reads
 * `COMMON PATH OF EGRESS TRAVEL DISTANCE` in the register may be plotted as
 * `COMMON PATH OF EGRESS`. Matching a distinctive opening recovers those, and
 * sixteen characters is long enough that it does not start landing on
 * unrelated text — "OCCUPANT LOAD" and "OCCUPANT LOAD FACTOR" share thirteen.
 */
const PREFIX = 16;

export function normalise(text: string): string {
  return text.replace(/\s+/g, ' ').trim().toUpperCase();
}

function union(a: Box, b: Box): Box {
  return {
    x0: Math.min(a.x0, b.x0),
    y0: Math.min(a.y0, b.y0),
    x1: Math.max(a.x1, b.x1),
    y1: Math.max(a.y1, b.y1),
  };
}

/**
 * Every place `anchor` appears on the page, in the order the text layer gives
 * them.
 *
 * The text layer splits a phrase across items freely — "COMMON PATH OF" and
 * "EGRESS TRAVEL" are routinely two — so this joins the items into one
 * normalised string, keeping a map from each character back to the item it came
 * from, searches that, and unions the boxes of the items each match covers.
 * Matching item by item would miss most real anchors.
 *
 * Order matters as much as the boxes do, because it is what `hit` indexes into.
 * Both sides walk the page's content stream in its own order — PyMuPDF's
 * `search_for` and pdf.js's `getTextContent` alike — so the nth occurrence here
 * is the nth occurrence there. It is an alignment rather than a guarantee: a
 * set that plots the same phrase twice in an order the two libraries recover
 * differently would disagree, and the register is what settles it.
 *
 * An empty result is a real answer and the caller must show it as one. A sheet
 * that pastes its code table in as a picture has no text layer to search, and
 * drawing a marker at a guessed position would put it on the wrong part of
 * somebody's drawing.
 */
export function locateAnchors(items: AnchorItem[], anchor: string): Box[] {
  const target = normalise(anchor);
  if (!target || !items.length) return [];

  let joined = '';
  const owner: number[] = [];
  items.forEach((item, index) => {
    const text = normalise(item.text);
    if (!text) return;
    joined += text + ' ';
    // One entry per character plus the joining space, so a match that runs
    // across the boundary attributes to both items rather than neither.
    for (let i = 0; i <= text.length; i += 1) owner.push(index);
  });

  let needle = target;
  let at = joined.indexOf(needle);
  if (at < 0 && target.length > PREFIX) {
    needle = target.slice(0, PREFIX);
    at = joined.indexOf(needle);
  }

  const found: Box[] = [];
  const width = Math.max(needle.length, 1);
  while (at >= 0) {
    let box: Box | null = null;
    for (const index of new Set(owner.slice(at, at + width))) {
      const item = items[index];
      if (!item) continue;
      box = box ? union(box, item.box) : item.box;
    }
    if (box) found.push(box);
    // Step past this match rather than one character on, so an anchor that can
    // overlap itself is still counted the way a reader would count it.
    at = joined.indexOf(needle, at + width);
  }
  return found;
}

/**
 * Where the `hit`-th occurrence of `anchor` sits, or null if it is not there.
 *
 * `hit` is the finding's own field and defaults to the first occurrence, which
 * is what it is on almost every finding. It is not a nicety: on a sheet whose
 * door schedule lists `2'-8"` four times, a viewer that always boxed the first
 * one would put its marker on a different door from the marked-up PDF, and the
 * two would be reporting the same finding about different rows.
 */
export function locateAnchor(
  items: AnchorItem[],
  anchor: string,
  hit = 0,
): Box | null {
  const found = locateAnchors(items, anchor);
  return found[hit] ?? null;
}
