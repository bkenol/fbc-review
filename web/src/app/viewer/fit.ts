/**
 * Choosing a zoom that puts the sheet in the window.
 *
 * Extracted from the viewer for the same reason `./anchor` was: it is pure
 * arithmetic, it is the part most likely to be subtly wrong, and testing it
 * through a component means standing up pdf.js and a canvas to check a
 * division.
 *
 * ## Why fitting is the default rather than 100%
 *
 * A permit sheet is 24x36 or 30x42 inches. At 100% in a 1400px window that is
 * about a ninth of the drawing, and the ninth you get is the corner the origin
 * happens to be in. Every reviewer's first action was to zoom out until they
 * could see the sheet, so the viewer does it for them — and keeps doing it as
 * the window and the side panel change size, until somebody zooms by hand and
 * takes the decision back.
 *
 * ## Why `width` is the mode it starts in
 *
 * `page` was the first default, and it was wrong in practice. Fitting a 24x36
 * sheet into a browser window alongside a side panel puts a schedule's row
 * height at two or three pixels: the sheet is *visible* and nothing on it is
 * *readable*, so the first act was always to zoom back in. `width` starts where
 * that zoom was going, and scrolling down a sheet is how the paper copy is read
 * anyway.
 *
 * `page` stays one click away, because "where am I on this sheet" is a real
 * question — it is just not the question you spend the session in.
 *
 * Fitting both axes is what makes one rule work for a landscape 24x36, a
 * portrait 8.5x11 title sheet and a square detail sheet without any of them
 * being a special case: the constraint is whichever axis runs out first.
 */

/** How the zoom is chosen. */
export type FitMode = 'page' | 'width' | 'manual';

/** Breathing room around the sheet inside the stage, in CSS pixels. */
export const STAGE_PAD = 24;

/**
 * Zoom bounds.
 *
 * The floor is low enough for a 42-inch sheet in a phone-width column; below it
 * the drawing is a grey rectangle. The ceiling is where the canvas allocation
 * for a large sheet stops being reasonable — at 6x a 42-inch sheet on a 2x
 * display is around 36000px on the long edge.
 */
export const MIN_ZOOM = 0.05;
export const MAX_ZOOM = 6;

export interface Size {
  width: number;
  height: number;
}

/**
 * The zoom that puts `extent` inside `box`.
 *
 * Returns `0` when there is nothing to fit — no page geometry yet, or a stage
 * that has not been laid out. `0` is the caller's signal to leave the zoom
 * alone rather than a scale to apply; setting a zoom of zero would blank the
 * canvas, and setting 1 would flash the sheet at full size for a frame.
 */
export function fitZoom(mode: FitMode, extent: Size, box: Size): number {
  if (mode === 'manual') return 0;
  if (!extent.width || !extent.height || !box.width || !box.height) return 0;

  const usableWidth = Math.max(box.width - STAGE_PAD * 2, 1);
  const usableHeight = Math.max(box.height - STAGE_PAD * 2, 1);
  const byWidth = usableWidth / extent.width;

  // `width` deliberately ignores the height and lets the sheet run off the
  // bottom: that is what you want on a schedule you are going to scroll down,
  // and what you never want on a floor plan.
  const scale = mode === 'width' ? byWidth : Math.min(byWidth, usableHeight / extent.height);
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, scale));
}
