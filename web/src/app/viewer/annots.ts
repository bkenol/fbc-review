/**
 * The comments and markup already inside the uploaded PDF.
 *
 * ## Why this exists
 *
 * A permit set arrives with other people's marks on it: the engineer's revision
 * clouds, a plans examiner's sticky notes from the last submittal, a callout
 * the architect left in. Those are the most valuable annotations on the sheet,
 * because somebody who knows the building wrote them — and until this module
 * the viewer showed none of them.
 *
 * That was not an oversight so much as a property of how the sheet is drawn.
 * The viewer paints the page onto a canvas, and pdf.js draws a canvas from the
 * page's content stream. Annotations are not in the content stream: they are a
 * separate array on the page object, drawn by a separate layer that this viewer
 * never had. A commented-up set and a clean one rendered identically.
 *
 * ## Why the geometry is rebuilt rather than handed to pdf.js's own layer
 *
 * `AnnotationLayer` produces HTML with its own stylesheet, its own hit
 * targeting and its own popup widgets, positioned by absolute CSS. The viewer
 * already has an overlay in PDF user space with findings and reviewer markup on
 * it, and every one of those is clickable in one consistent way. Two layers
 * with two idioms would mean a comment you read by hovering next to a finding
 * you read by clicking, and a popup that scrolls out of register with the sheet
 * it belongs to.
 *
 * So the geometry is converted here and drawn as SVG in the same space as
 * everything else. The trade is that this understands the annotation types it
 * has been taught and falls back to the annotation's own rectangle for the
 * rest — which is what `AnnotationLayer` does for unknown subtypes too, and is
 * never a guess: the rectangle is the annotation's declared extent.
 *
 * ## Pure, and tested as such
 *
 * Nothing here imports pdf.js. It takes the plain objects `getAnnotations()`
 * returns and a function that maps PDF user space to viewport space, which is
 * the one thing only pdf.js can supply. That keeps the shape derivation — the
 * part with real arithmetic in it — testable without a canvas.
 */
import { Box } from './anchor';

/** Maps a point in PDF user space to the viewport space the overlay draws in. */
export type ToViewport = (x: number, y: number) => [number, number];

/** What kind of mark this is, for styling and for what the register calls it. */
export type AnnotKind =
  | 'note'
  | 'shape'
  | 'cloud'
  | 'ink'
  | 'textmark'
  | 'label'
  | 'stamp'
  | 'other';

export type AnnotShape =
  | { kind: 'rect'; box: Box }
  | { kind: 'ellipse'; box: Box }
  | { kind: 'path'; points: number[][]; closed: boolean }
  | { kind: 'quads'; boxes: Box[] };

export interface PageAnnotation {
  id: string;
  /** The PDF's own subtype, kept verbatim: it is what the file says this is. */
  subtype: string;
  kind: AnnotKind;
  /** What to call it in a list, in the reader's language rather than the spec's. */
  label: string;
  /** The area of the drawing this mark is about, in viewport space. */
  box: Box;
  /** The mark's own geometry. Empty when only its rectangle is known. */
  shapes: AnnotShape[];
  comment: string;
  author: string;
  /** The colour the file gave it, as a CSS value, or '' for the default ink. */
  colour: string;
}

/**
 * Subtypes that are never drawn.
 *
 * `Link` is navigation, not a mark. `Popup` is the window a markup annotation
 * opens, not a second annotation — its text belongs to its parent and drawing
 * it would double every comment. `Widget` is a form field, and on a permit set
 * that is the digital signature block, which is already visible on the sheet.
 */
const SKIP = new Set(['Link', 'Popup', 'Widget']);

const LABELS: Record<string, [AnnotKind, string]> = {
  Text: ['note', 'Comment'],
  FreeText: ['label', 'Text on the sheet'],
  Square: ['shape', 'Box'],
  Circle: ['shape', 'Ellipse'],
  Line: ['shape', 'Line'],
  Polygon: ['cloud', 'Outlined area'],
  PolyLine: ['cloud', 'Outline'],
  Ink: ['ink', 'Freehand'],
  Highlight: ['textmark', 'Highlight'],
  Underline: ['textmark', 'Underline'],
  StrikeOut: ['textmark', 'Strike-out'],
  Squiggly: ['textmark', 'Squiggle'],
  Stamp: ['stamp', 'Stamp'],
  Caret: ['note', 'Insertion mark'],
  FileAttachment: ['note', 'Attachment'],
};

/**
 * One annotation as pdf.js hands it over.
 *
 * Typed loosely on purpose. `getAnnotations()` is declared to return `any[]`,
 * the fields present vary by subtype, and their container types have changed
 * across pdf.js releases — `quadPoints` has been an array of point objects and
 * is now a flat `Float32Array`. Reading them defensively here is what keeps a
 * patch release of pdf.js from silently emptying this layer.
 */
export interface RawAnnotation {
  id?: string;
  subtype?: string;
  rect?: number[] | Float32Array;
  contents?: string;
  contentsObj?: { str?: string };
  title?: string;
  titleObj?: { str?: string };
  color?: number[] | Uint8ClampedArray | null;
  quadPoints?: unknown;
  vertices?: unknown;
  inkLists?: unknown;
  lineCoordinates?: number[] | Float32Array;
}

/**
 * Any of the containers pdf.js has used for a run of coordinates, as pairs.
 *
 * Accepts a flat list, a list of pairs, and a list of `{x, y}` objects, because
 * across versions and subtypes it has been all three. An odd-length flat list
 * drops its trailing number rather than emitting a point with an undefined
 * coordinate.
 */
export function pairs(raw: unknown): number[][] {
  if (!raw) return [];
  const list = Array.isArray(raw) ? raw : ArrayBuffer.isView(raw) ? Array.from(raw as never) : [];
  const out: number[][] = [];
  for (const entry of list as unknown[]) {
    if (typeof entry === 'number') continue;
    if (Array.isArray(entry) && entry.length >= 2) {
      out.push([Number(entry[0]), Number(entry[1])]);
    } else if (entry && typeof entry === 'object' && 'x' in entry && 'y' in entry) {
      const point = entry as { x: number; y: number };
      out.push([Number(point.x), Number(point.y)]);
    }
  }
  if (out.length) return out;

  const flat = (list as unknown[]).filter((n): n is number => typeof n === 'number');
  for (let i = 0; i + 1 < flat.length; i += 2) out.push([flat[i], flat[i + 1]]);
  return out;
}

function box(points: number[][]): Box {
  const xs = points.map(([x]) => x);
  const ys = points.map(([, y]) => y);
  return {
    x0: Math.min(...xs),
    y0: Math.min(...ys),
    x1: Math.max(...xs),
    y1: Math.max(...ys),
  };
}

/**
 * The annotation's rectangle in viewport space.
 *
 * Both corners are mapped rather than the numbers reordered, because the
 * viewport transform flips the y axis: a rectangle that is bottom-up in PDF
 * user space is top-down on screen, and normalising before the mapping would
 * produce a box that is correct on one axis and inverted on the other.
 */
function rectOf(raw: number[] | Float32Array | undefined, to: ToViewport): Box | null {
  const r = raw ? Array.from(raw as ArrayLike<number>) : [];
  if (r.length < 4 || r.some((n) => !Number.isFinite(n))) return null;
  return box([to(r[0], r[1]), to(r[2], r[3])]);
}

function colourOf(raw: RawAnnotation['color']): string {
  if (!raw) return '';
  const [r, g, b] = Array.from(raw as ArrayLike<number>);
  if (![r, g, b].every((n) => Number.isFinite(n))) return '';
  return `rgb(${r} ${g} ${b})`;
}

/** Text-markup quads arrive as four corners each; each quad becomes one box. */
function quadBoxes(raw: unknown, to: ToViewport): Box[] {
  const points = pairs(raw).map(([x, y]) => to(x, y));
  const boxes: Box[] = [];
  for (let i = 0; i + 3 < points.length; i += 4) boxes.push(box(points.slice(i, i + 4)));
  return boxes;
}

function shapesFor(annot: RawAnnotation, subtype: string, to: ToViewport, extent: Box): AnnotShape[] {
  const map = (raw: unknown): number[][] => pairs(raw).map(([x, y]) => to(x, y));

  switch (subtype) {
    case 'Square':
      return [{ kind: 'rect', box: extent }];
    case 'Circle':
      return [{ kind: 'ellipse', box: extent }];
    case 'Line': {
      const points = map(annot.lineCoordinates);
      return points.length >= 2 ? [{ kind: 'path', points, closed: false }] : [];
    }
    case 'Polygon':
    case 'PolyLine': {
      const points = map(annot.vertices);
      return points.length >= 2
        ? [{ kind: 'path', points, closed: subtype === 'Polygon' }]
        : [];
    }
    case 'Ink': {
      const lists = Array.isArray(annot.inkLists) ? annot.inkLists : [];
      return lists
        .map((list) => map(list))
        .filter((points) => points.length >= 2)
        .map((points) => ({ kind: 'path', points, closed: false }) as AnnotShape);
    }
    case 'Highlight':
    case 'Underline':
    case 'StrikeOut':
    case 'Squiggly': {
      const boxes = quadBoxes(annot.quadPoints, to);
      return boxes.length ? [{ kind: 'quads', boxes }] : [];
    }
    default:
      return [];
  }
}

/**
 * Every mark on this page that is worth drawing, in the file's own order.
 *
 * An annotation with no readable rectangle is dropped rather than placed at the
 * origin — the same rule the finding pins follow. A mark in the corner of a
 * sheet it does not belong on is worse than one that is only in the list.
 */
export function readAnnotations(raw: RawAnnotation[], to: ToViewport): PageAnnotation[] {
  const out: PageAnnotation[] = [];

  raw.forEach((annot, index) => {
    const subtype = annot.subtype ?? '';
    if (SKIP.has(subtype)) return;

    const extent = rectOf(annot.rect, to);
    if (!extent) return;

    const shapes = shapesFor(annot, subtype, to, extent);
    const [kind, label] = LABELS[subtype] ?? (['other', subtype || 'Annotation'] as const);
    const comment = (annot.contentsObj?.str ?? annot.contents ?? '').trim();
    const author = (annot.titleObj?.str ?? annot.title ?? '').trim();

    out.push({
      // pdf.js gives every annotation an id, but it is the object number and a
      // damaged file can repeat one. The index keeps the key unique for a
      // template's track expression without inventing a different identity.
      id: `${annot.id || subtype || 'annot'}-${index}`,
      subtype,
      kind,
      label,
      box: shapes.length ? unionAll(shapes, extent) : extent,
      shapes,
      comment,
      author,
      colour: colourOf(annot.color),
    });
  });

  return out;
}

/**
 * The extent actually drawn.
 *
 * An ink annotation's rectangle is often looser than the strokes inside it, and
 * a text-markup annotation's covers every quad. Boxing what was drawn rather
 * than what was declared is what makes "scroll this comment into view" land on
 * the mark instead of near it.
 */
function unionAll(shapes: AnnotShape[], fallback: Box): Box {
  const points: number[][] = [];
  for (const shape of shapes) {
    if (shape.kind === 'path') points.push(...shape.points);
    else if (shape.kind === 'quads') {
      for (const b of shape.boxes) points.push([b.x0, b.y0], [b.x1, b.y1]);
    } else points.push([shape.box.x0, shape.box.y0], [shape.box.x1, shape.box.y1]);
  }
  return points.length ? box(points) : fallback;
}

/** An SVG path for a run of points, closed for a polygon. */
export function pathOf(points: number[][], closed = false): string {
  if (!points.length) return '';
  const d = points.map(([x, y], i) => `${i ? 'L' : 'M'}${x} ${y}`).join(' ');
  return closed ? `${d} Z` : d;
}

/**
 * A comment reduced to a few short lines, for drawing on the sheet itself.
 *
 * A comment is only visible on the drawing if it is drawn there, and SVG text
 * does not wrap — so the wrapping is done here, where it can be tested, rather
 * than by eye in a template. A word longer than the line is left whole and
 * allowed to overhang rather than broken: a split sheet number or dimension is
 * harder to read than a slightly long line.
 *
 * The last line is elided when there is more to say, and that elision is the
 * point. The sheet gets enough of the comment to know it is there and what it
 * is about; the panel beside it has the whole thing.
 */
export function captionLines(text: string, width = 34, lines = 2): string[] {
  const words = (text || '').replace(/\s+/g, ' ').trim().split(' ').filter(Boolean);
  if (!words.length || lines < 1) return [];

  const out: string[] = [];
  let line = '';
  let taken = 0;
  for (const word of words) {
    const next = line ? `${line} ${word}` : word;
    if (next.length <= width || !line) {
      line = next;
      taken += 1;
      continue;
    }
    out.push(line);
    line = word;
    taken += 1;
    if (out.length === lines) break;
  }
  if (out.length < lines && line) out.push(line);
  else if (out.length === lines) taken -= 1;

  if (taken < words.length) {
    const last = out[out.length - 1];
    out[out.length - 1] =
      last.length + 2 > width ? `${last.slice(0, Math.max(1, width - 1))}…` : `${last} …`;
  }
  return out;
}
