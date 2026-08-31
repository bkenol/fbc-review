import { describe, expect, it } from 'vitest';

import { MAX_ZOOM, MIN_ZOOM, STAGE_PAD, fitZoom } from './fit';

/** A 24x36 in. sheet in PDF points, landscape — the common permit plot. */
const LANDSCAPE = { width: 2592, height: 1728 };
/** Letter portrait, which a title or cover sheet often is. */
const PORTRAIT = { width: 612, height: 792 };

/** A stage of the given size, plus the padding the fit reserves. */
function stage(width: number, height: number) {
  return { width: width + STAGE_PAD * 2, height: height + STAGE_PAD * 2 };
}

describe('fitZoom', () => {
  it('shows the whole sheet, not the corner of it', () => {
    // 1296x864 of usable stage against a 2592x1728 sheet: exactly half.
    expect(fitZoom('page', LANDSCAPE, stage(1296, 864))).toBeCloseTo(0.5);
  });

  it('is limited by whichever axis runs out first', () => {
    // Plenty of width, not enough height. Fitting on width alone would put the
    // bottom third of the drawing below the fold, which is the failure this
    // whole mode exists to prevent.
    const zoom = fitZoom('page', LANDSCAPE, stage(2592, 432));
    expect(zoom).toBeCloseTo(0.25);
    expect(LANDSCAPE.height * zoom).toBeLessThanOrEqual(432);
  });

  it('handles a portrait sheet with the same rule and no special case', () => {
    // Width is the constraint here, height was on the landscape sheet. One
    // expression covers both, which is why orientation is never branched on.
    expect(fitZoom('page', PORTRAIT, stage(306, 792))).toBeCloseTo(0.5);
  });

  it('fits the width and lets a long sheet scroll', () => {
    const zoom = fitZoom('width', LANDSCAPE, stage(1296, 100));
    expect(zoom).toBeCloseTo(0.5);
    // Deliberately taller than the stage: a schedule is read by scrolling.
    expect(LANDSCAPE.height * zoom).toBeGreaterThan(100);
  });

  it('never returns a scale outside the bounds', () => {
    expect(fitZoom('page', LANDSCAPE, stage(2, 2))).toBe(MIN_ZOOM);
    expect(fitZoom('page', { width: 1, height: 1 }, stage(9000, 9000))).toBe(MAX_ZOOM);
  });

  it('leaves the zoom alone when there is nothing to fit', () => {
    // Zero is the caller's signal to do nothing. Returning 1 instead would
    // flash a 36-inch sheet at full size for a frame on every load.
    expect(fitZoom('page', LANDSCAPE, { width: 0, height: 0 })).toBe(0);
    expect(fitZoom('page', { width: 0, height: 0 }, stage(800, 600))).toBe(0);
  });

  it('does nothing at all once the reviewer has taken the zoom over', () => {
    expect(fitZoom('manual', LANDSCAPE, stage(1296, 864))).toBe(0);
  });

  it('leaves room around the sheet rather than filling to the edge', () => {
    // A drawing whose border is flush against the scroll box reads as cropped.
    const zoom = fitZoom('page', LANDSCAPE, stage(1296, 864));
    expect(LANDSCAPE.width * zoom).toBeLessThan(1296 + STAGE_PAD * 2);
  });
});
