import { describe, expect, it } from 'vitest';

import { RawAnnotation, captionLines, pairs, pathOf, readAnnotations } from './annots';

/**
 * The scale-1 viewport map for a 792pt-tall page: x through, y flipped.
 *
 * That flip is the whole reason the conversion is not "copy the numbers": a
 * PDF rectangle is bottom-up and the overlay is top-down.
 */
const to = (x: number, y: number): [number, number] => [x, 792 - y];

function annot(over: Partial<RawAnnotation> = {}): RawAnnotation {
  return { id: '1R', subtype: 'Square', rect: [100, 600, 200, 700], ...over };
}

describe('readAnnotations', () => {
  it('boxes a square where it is on screen, not where its numbers are', () => {
    const [mark] = readAnnotations([annot()], to);
    expect(mark.box).toEqual({ x0: 100, y0: 92, x1: 200, y1: 192 });
    expect(mark.shapes).toEqual([{ kind: 'rect', box: mark.box }]);
  });

  it('carries the comment and who wrote it', () => {
    const [mark] = readAnnotations(
      [annot({ contentsObj: { str: '  Verify clear width  ' }, titleObj: { str: 'M. Tobias' } })],
      to,
    );
    expect(mark.comment).toBe('Verify clear width');
    expect(mark.author).toBe('M. Tobias');
  });

  it('keeps the colour the file gave the mark', () => {
    // A drafter's red cloud stays red. The colour is a claim the file makes
    // about its own mark, and recolouring it to the app's ink would erase it.
    const [mark] = readAnnotations([annot({ color: [255, 0, 0] })], to);
    expect(mark.colour).toBe('rgb(255 0 0)');
    expect(readAnnotations([annot({ color: null })], to)[0].colour).toBe('');
  });

  it('draws freehand ink as its strokes, one path per stroke', () => {
    const [mark] = readAnnotations(
      [annot({ subtype: 'Ink', inkLists: [[10, 700, 20, 690], [40, 600, 50, 590, 60, 580]] })],
      to,
    );
    expect(mark.shapes).toHaveLength(2);
    expect(mark.shapes[0]).toEqual({ kind: 'path', points: [[10, 92], [20, 102]], closed: false });
    expect(mark.kind).toBe('ink');
  });

  it('closes a polygon and leaves a polyline open', () => {
    const vertices = [10, 700, 60, 700, 60, 650];
    const [poly] = readAnnotations([annot({ subtype: 'Polygon', vertices })], to);
    const [line] = readAnnotations([annot({ subtype: 'PolyLine', vertices })], to);
    expect((poly.shapes[0] as { closed: boolean }).closed).toBe(true);
    expect((line.shapes[0] as { closed: boolean }).closed).toBe(false);
  });

  it('turns text-markup quads into one box each', () => {
    const quad = [10, 700, 60, 700, 10, 690, 60, 690];
    const [mark] = readAnnotations(
      [annot({ subtype: 'Highlight', quadPoints: [...quad, ...quad.map((n) => n + 100)] })],
      to,
    );
    expect(mark.shapes[0].kind).toBe('quads');
    expect((mark.shapes[0] as { boxes: unknown[] }).boxes).toHaveLength(2);
  });

  it('boxes what was drawn rather than what was declared', () => {
    // An ink annotation's rectangle is routinely looser than the strokes in it.
    // Scrolling a comment into view has to land on the mark, not near it.
    const [mark] = readAnnotations(
      [annot({ subtype: 'Ink', rect: [0, 500, 400, 780], inkLists: [[10, 700, 20, 690]] })],
      to,
    );
    expect(mark.box).toEqual({ x0: 10, y0: 92, x1: 20, y1: 102 });
  });

  it('skips links, popups and form widgets', () => {
    // A popup carries its parent's text; drawing it would double every comment.
    const marks = readAnnotations(
      [annot({ subtype: 'Link' }), annot({ subtype: 'Popup' }), annot({ subtype: 'Widget' })],
      to,
    );
    expect(marks).toEqual([]);
  });

  it('falls back to the declared rectangle for a subtype it does not know', () => {
    const [mark] = readAnnotations([annot({ subtype: 'Redact' })], to);
    expect(mark.kind).toBe('other');
    expect(mark.label).toBe('Redact');
    expect(mark.box).toEqual({ x0: 100, y0: 92, x1: 200, y1: 192 });
  });

  it('drops a mark with no readable rectangle rather than placing it at the origin', () => {
    expect(readAnnotations([annot({ rect: undefined })], to)).toEqual([]);
    expect(readAnnotations([annot({ rect: [1, 2] })], to)).toEqual([]);
    expect(readAnnotations([annot({ rect: [0, 0, NaN, 10] })], to)).toEqual([]);
  });

  it('gives every mark a distinct key even when the file repeats an id', () => {
    const marks = readAnnotations([annot(), annot()], to);
    expect(marks[0].id).not.toBe(marks[1].id);
  });
});

describe('pairs', () => {
  it('reads the containers pdf.js has used, across versions', () => {
    expect(pairs([1, 2, 3, 4])).toEqual([[1, 2], [3, 4]]);
    expect(pairs(new Float32Array([1, 2, 3, 4]))).toEqual([[1, 2], [3, 4]]);
    expect(pairs([{ x: 1, y: 2 }])).toEqual([[1, 2]]);
    expect(pairs([[1, 2]])).toEqual([[1, 2]]);
  });

  it('drops a trailing number rather than emitting half a point', () => {
    expect(pairs([1, 2, 3])).toEqual([[1, 2]]);
  });

  it('is empty for nothing', () => {
    expect(pairs(undefined)).toEqual([]);
    expect(pairs([])).toEqual([]);
  });
});

describe('pathOf', () => {
  it('writes a path, closing it only when asked', () => {
    expect(pathOf([[0, 0], [10, 5]])).toBe('M0 0 L10 5');
    expect(pathOf([[0, 0], [10, 5]], true)).toBe('M0 0 L10 5 Z');
    expect(pathOf([])).toBe('');
  });
});

describe('captionLines', () => {
  it('wraps a comment onto the lines it is given', () => {
    expect(captionLines('door 104 is a 2-8 leaf', 12, 2)).toEqual(['door 104 is', 'a 2-8 leaf']);
  });

  it('says there is more rather than stopping mid-sentence', () => {
    const lines = captionLines('one two three four five six seven eight', 10, 2);
    expect(lines).toHaveLength(2);
    expect(lines[1].endsWith('…')).toBe(true);
  });

  it('does not elide a comment that fits', () => {
    expect(captionLines('verify clear width', 40, 2)).toEqual(['verify clear width']);
  });

  it('leaves a word longer than the line whole', () => {
    // Breaking a sheet number or a dimension is worse than overhanging.
    expect(captionLines('COMMON-PATH-OF-EGRESS-TRAVEL', 8, 2)).toEqual([
      'COMMON-PATH-OF-EGRESS-TRAVEL',
    ]);
  });

  it('is empty for nothing to say', () => {
    expect(captionLines('')).toEqual([]);
    expect(captionLines('   ')).toEqual([]);
  });
});
