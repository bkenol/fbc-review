import { describe, expect, it } from 'vitest';

import { AnchorItem, locateAnchor, locateAnchors, normalise } from './anchor';

/** A text run at a position, as the page's text layer would hand it over. */
function item(text: string, x = 0, y = 100, width = 60): AnchorItem {
  return { text, box: { x0: x, y0: y - 8, x1: x + width, y1: y } };
}

describe('locateAnchor', () => {
  it('finds an anchor that sits inside one run of text', () => {
    const box = locateAnchor([item('OCCUPANT LOAD 70', 40, 200)], 'OCCUPANT LOAD 70');
    expect(box).toEqual({ x0: 40, y0: 192, x1: 100, y1: 200 });
  });

  it('spans an anchor split across runs, which is the common case', () => {
    // The text layer breaks phrases wherever the PDF's own show-text operators
    // do. Matching run by run would miss most real anchors.
    const box = locateAnchor(
      [item('COMMON PATH OF', 10, 100, 50), item('EGRESS TRAVEL', 70, 100, 40)],
      'COMMON PATH OF EGRESS TRAVEL',
    );
    expect(box).toEqual({ x0: 10, y0: 92, x1: 110, y1: 100 });
  });

  it('ignores the difference between how the two sides write whitespace', () => {
    const box = locateAnchor([item('COMMON   PATH\nOF EGRESS')], 'common path of egress');
    expect(box).not.toBeNull();
  });

  it('falls back to a distinctive opening when the sheet abbreviates', () => {
    const box = locateAnchor(
      [item('COMMON PATH OF EGRESS')],
      'COMMON PATH OF EGRESS TRAVEL DISTANCE',
    );
    expect(box).not.toBeNull();
  });

  it('does not match a short anchor on a prefix', () => {
    // The prefix fallback is only for anchors long enough that an opening is
    // distinctive. Without that floor, "EXIT" would match "EXITING SCHEDULE".
    expect(locateAnchor([item('EXITING SCHEDULE')], 'EXIT SIGN')).toBeNull();
  });

  it('returns null rather than a guess when the text is not on the page', () => {
    // A sheet that pastes its code table in as a picture has nothing to search.
    // Drawing a marker at a guessed position would put it on the wrong part of
    // somebody's drawing, which is worse than not drawing one.
    expect(locateAnchor([item('GENERAL NOTES')], 'OUTDOOR AIR CFM')).toBeNull();
    expect(locateAnchor([], 'ANYTHING')).toBeNull();
    expect(locateAnchor([item('SOMETHING')], '')).toBeNull();
  });

  it('skips empty runs without losing the mapping back to the boxes', () => {
    const box = locateAnchor(
      [item('  ', 0, 100, 5), item('DOOR 104', 200, 300, 30)],
      'DOOR 104',
    );
    expect(box).toEqual({ x0: 200, y0: 292, x1: 230, y1: 300 });
  });
});

describe('locateAnchors and hit', () => {
  it('returns every occurrence, in the order the page gives them', () => {
    const boxes = locateAnchors(
      [item("2'-8\"", 10, 100, 20), item('DOOR', 40, 100, 20), item("2'-8\"", 10, 200, 20)],
      "2'-8\"",
    );
    expect(boxes).toHaveLength(2);
    expect(boxes[0].y1).toBe(100);
    expect(boxes[1].y1).toBe(200);
  });

  it("boxes the occurrence the finding's hit names, as the renderer does", () => {
    // A door schedule listing the same leaf width four times is the case this
    // is for: boxing the first row when the finding is about the third puts
    // the viewer's marker on a different door from the marked-up PDF.
    const items = [item("2'-8\"", 10, 100, 20), item("2'-8\"", 10, 200, 20)];
    expect(locateAnchor(items, "2'-8\"", 1)?.y1).toBe(200);
    expect(locateAnchor(items, "2'-8\"", 0)?.y1).toBe(100);
  });

  it('is null when the hit is past the last occurrence', () => {
    // Which is what the renderer does too: `len(hits) <= f.hit` draws nothing.
    expect(locateAnchor([item('DOOR 104')], 'DOOR 104', 3)).toBeNull();
  });

  it('finds nothing rather than everything for an anchor that is not there', () => {
    expect(locateAnchors([item('GENERAL NOTES')], 'OUTDOOR AIR')).toEqual([]);
  });
});

describe('normalise', () => {
  it('collapses whitespace and folds case, and nothing else', () => {
    expect(normalise('  Common   Path\n of\tEgress ')).toBe('COMMON PATH OF EGRESS');
    // Section citations are the most stable token on a sheet; punctuation in
    // them has to survive.
    expect(normalise('(1006.2.1)')).toBe('(1006.2.1)');
  });
});
