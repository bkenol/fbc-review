/**
 * Finding an engine anchor on a rendered page.
 *
 * A `Finding` says which sheet it is about and carries an `anchor` — the text
 * to box on the drawing — and no coordinates, because the engine's renderer
 * searches for that text when it draws the marked-up PDF. The viewer has to do
 * the same search to put the same marker in the same place.
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
 * Where `anchor` sits on the page, or null if it is not there.
 *
 * The text layer splits a phrase across items freely — "COMMON PATH OF" and
 * "EGRESS TRAVEL" are routinely two — so this joins the items into one
 * normalised string, keeping a map from each character back to the item it came
 * from, searches that, and unions the boxes of the items the match covers.
 * Matching item by item would miss most real anchors.
 *
 * Returning null is a real answer and the caller must show it as one. A sheet
 * that pastes its code table in as a picture has no text layer to search, and
 * drawing a marker at a guessed position would put it on the wrong part of
 * somebody's drawing.
 */
export function locateAnchor(items: AnchorItem[], anchor: string): Box | null {
  const target = normalise(anchor);
  if (!target || !items.length) return null;

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

  let at = joined.indexOf(target);
  let length = target.length;
  if (at < 0 && target.length > PREFIX) {
    length = PREFIX;
    at = joined.indexOf(target.slice(0, PREFIX));
  }
  if (at < 0) return null;

  let box: Box | null = null;
  for (const index of new Set(owner.slice(at, at + Math.max(length, 1)))) {
    const item = items[index];
    if (!item) continue;
    box = box ? union(box, item.box) : item.box;
  }
  return box;
}
