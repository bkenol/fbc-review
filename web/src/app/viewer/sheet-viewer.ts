/**
 * The sheet viewer: a permit set, its findings, and what you drew on it.
 *
 * ## Why the source set and not the marked-up PDF
 *
 * The review already produces a marked-up PDF with every finding burnt into the
 * page. Rendering that here and drawing an interactive layer on top would show
 * every marker twice, and neither copy could be turned off. So this renders the
 * set as uploaded and draws the findings itself — which is also what makes them
 * clickable.
 *
 * ## One coordinate space, and it is the PDF's
 *
 * The SVG overlay's `viewBox` is the page's own viewport at scale 1, so
 * everything drawn on it — finding pins, markup, the shape being dragged right
 * now — is expressed in PDF points and the browser does the scaling. Zoom
 * changes the canvas resolution and the element's CSS size and nothing else.
 *
 * That is why `MarkupGeometry` is stored in PDF points rather than pixels: a
 * markup recorded in device coordinates is in the wrong place at every zoom
 * level but the one it was drawn at, and on a 24x36 sheet where a door tag is a
 * few points across, "roughly right" is not right.
 *
 * ## Finding pins are located the way the engine locates them
 *
 * A `Finding` carries a sheet and an `anchor` — the text to box on the drawing —
 * and no coordinates, because the engine's renderer searches for that text at
 * render time. This does the same search against the page's text layer. When
 * the anchor cannot be found the finding is still listed, and says it could not
 * be placed. Guessing a position would put a marker on the wrong part of
 * somebody's drawing, which is worse than not drawing one.
 */
import {
  Component,
  ElementRef,
  computed,
  effect,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import * as pdfjs from 'pdfjs-dist';
import type { PDFDocumentProxy, PDFPageProxy } from 'pdfjs-dist';

/**
 * Types derived from the methods that return them.
 *
 * `TextItem` and `PageViewport` are real types in pdfjs-dist but are not
 * re-exported from the package entry point, so naming them directly would mean
 * importing from `pdfjs-dist/types/src/...` — a path into the package's
 * internals that a patch release is free to move. Deriving them from the public
 * method signatures gives the same types and cannot rot.
 */
type Viewport = ReturnType<PDFPageProxy['getViewport']>;
type ContentItem = Awaited<ReturnType<PDFPageProxy['getTextContent']>>['items'][number];
type TextItem = Extract<ContentItem, { str: string }>;

import { Finding, Markup, MarkupKind, MarkupKindInfo, MarkupRequest } from '../api';
import { AnchorItem, Box, locateAnchor } from './anchor';

// Same origin, copied out of the package by the build (see angular.json). The
// Hosting CSP is `default-src 'self'` with no CDN, so a worker from anywhere
// else is blocked with no visible error.
pdfjs.GlobalWorkerOptions.workerSrc = 'pdf.worker.min.mjs';

/** The select tool draws nothing; it picks what is already there. */
export const SELECT = 'select';

export type Tool = typeof SELECT | MarkupKind;

export interface PlacedFinding {
  finding: Finding;
  /** Null when the anchor text could not be found on the page. */
  box: Box | null;
}

export type { Box } from './anchor';

interface Draft {
  kind: MarkupKind;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  points: number[][];
}

/** A note pin has no drag; anything smaller than this was a click, not a shape. */
const MIN_DRAG = 4;

@Component({
  selector: 'app-sheet-viewer',
  templateUrl: './sheet-viewer.html',
})
export class SheetViewer {
  /** Signed URL for the set as uploaded. */
  readonly src = input.required<string>();
  readonly findings = input<Finding[]>([]);
  readonly markups = input<Markup[]>([]);
  readonly markupKinds = input<MarkupKindInfo[]>([]);
  /** Training mode. Without it the viewer reads and does not draw. */
  readonly canDraw = input(false);
  readonly selectedFid = input<string>('');

  readonly findingPicked = output<Finding>();
  readonly markupDrawn = output<MarkupRequest>();
  readonly markupPicked = output<Markup>();

  private readonly canvasRef = viewChild<ElementRef<HTMLCanvasElement>>('canvas');
  private readonly overlayRef = viewChild<ElementRef<SVGSVGElement>>('overlay');

  protected readonly page = signal(1);
  protected readonly pages = signal(0);
  protected readonly zoom = signal(1);
  protected readonly loading = signal(true);
  protected readonly rendering = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly tool = signal<Tool>(SELECT);
  protected readonly draft = signal<Draft | null>(null);
  protected readonly select = SELECT;

  /** The page box in PDF points, which is also the overlay's viewBox. */
  protected readonly extent = signal<{ width: number; height: number }>({
    width: 612,
    height: 792,
  });

  private document: PDFDocumentProxy | null = null;
  // Kept because destroy() lives on the loading task, not the document: it is
  // what tears down the worker, and leaking one per opened set would leave a
  // thread per sheet the reviewer ever looked at.
  private loading_task: ReturnType<typeof pdfjs.getDocument> | null = null;
  private currentPage: PDFPageProxy | null = null;
  private renderToken = 0;
  /** Anchor boxes are the same for the life of a page; the search is not free. */
  private readonly located = signal<Map<string, Box | null>>(new Map());

  protected readonly viewBox = computed(() => {
    const { width, height } = this.extent();
    return `0 0 ${width} ${height}`;
  });

  protected readonly cssWidth = computed(() => this.extent().width * this.zoom());
  protected readonly cssHeight = computed(() => this.extent().height * this.zoom());

  /** Findings on the page being shown, with wherever they could be placed. */
  protected readonly placed = computed<PlacedFinding[]>(() => {
    const boxes = this.located();
    return this.findings()
      .filter((f) => f.page === this.page())
      .map((finding) => ({ finding, box: boxes.get(finding.fid) ?? null }));
  });

  protected readonly unplaced = computed(() => this.placed().filter((p) => !p.box).length);
  protected readonly pageMarkups = computed(() =>
    this.markups().filter((m) => m.page === this.page()),
  );

  constructor() {
    // Reload when the URL changes. A signed URL is refreshed when it expires,
    // so this fires on more than a change of document.
    effect(() => {
      const url = this.src();
      if (url) void this.load(url);
    });

    // Re-render on page or zoom. `renderToken` makes a superseded render
    // discard its own result: pages of a large set take long enough that a
    // fast click-through can otherwise land an earlier page on the canvas
    // after a later one.
    effect(() => {
      this.page();
      this.zoom();
      this.canvasRef();
      void this.draw();
    });
  }

  // ── loading ─────────────────────────────────────────────────────────────
  private async load(url: string): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      await this.loading_task?.destroy();
      this.loading_task = pdfjs.getDocument({
        url,
        // Both directories are copied out of the package by the build and
        // served from this origin — see angular.json. Left unset, pdf.js looks
        // for them on a CDN, which the Hosting CSP blocks.
        wasmUrl: 'pdf-wasm/',
        standardFontDataUrl: 'pdf-fonts/',
        // Nothing here enables PDF-embedded JavaScript: running it is a
        // viewer-layer feature of pdf.js that this component never wires up,
        // and the interpreter it would need is deliberately not shipped (see
        // angular.json). An uploaded permit set is untrusted input.
      });
      this.document = await this.loading_task.promise;
      this.pages.set(this.document.numPages);
      this.page.set(1);
      this.loading.set(false);
      await this.draw();
    } catch {
      this.document = null;
      this.loading.set(false);
      // Deliberately not the exception text: it can carry the signed URL, and
      // a signed URL in a visible error is a credential on screen.
      this.error.set('This set could not be opened for viewing. The downloads below still work.');
    }
  }

  private async draw(): Promise<void> {
    const canvas = this.canvasRef()?.nativeElement;
    if (!this.document || !canvas) return;

    const token = ++this.renderToken;
    this.rendering.set(true);
    try {
      const page = await this.document.getPage(this.page());
      if (token !== this.renderToken) return;
      this.currentPage = page;

      const base = page.getViewport({ scale: 1 });
      this.extent.set({ width: base.width, height: base.height });

      // Render at device resolution and let CSS size it down, or a sheet at
      // 200% on a retina display is a blurred photograph of a drawing.
      const ratio = window.devicePixelRatio || 1;
      const viewport = page.getViewport({ scale: this.zoom() * ratio });
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);

      const context = canvas.getContext('2d');
      if (!context) return;
      await page.render({ canvas, canvasContext: context, viewport }).promise;
      if (token !== this.renderToken) return;

      await this.locateAnchors(page, base, token);
    } catch {
      if (token === this.renderToken) {
        this.error.set('This sheet could not be drawn.');
      }
    } finally {
      if (token === this.renderToken) this.rendering.set(false);
    }
  }

  /**
   * Find each finding's anchor text on this page.
   *
   * This end of the job is the pdf.js part — turning text items into boxes in
   * viewport space. The search itself is in `./anchor`, where it can be tested
   * without a canvas.
   */
  private async locateAnchors(
    page: PDFPageProxy,
    viewport: Viewport,
    token: number,
  ): Promise<void> {
    const wanted = this.findings().filter((f) => f.page === this.page() && f.anchor);
    if (!wanted.length) {
      this.located.set(new Map());
      return;
    }

    const content = await page.getTextContent();
    if (token !== this.renderToken) return;

    const items: AnchorItem[] = content.items
      .filter((item): item is TextItem => 'str' in item && !!item.str.trim())
      .map((item) => {
        // applyTransform writes into the point it is given and returns nothing.
        const point: number[] = [item.transform[4], item.transform[5]];
        pdfjs.Util.applyTransform(point, viewport.transform);
        const [x, y] = point;
        // The transform's origin is the text baseline, so the box runs upward
        // from it in viewport space.
        return {
          text: item.str,
          box: { x0: x, y0: y - item.height, x1: x + item.width, y1: y },
        };
      });

    const found = new Map<string, Box | null>();
    for (const finding of wanted) {
      found.set(finding.fid, locateAnchor(items, finding.anchor));
    }

    if (token === this.renderToken) this.located.set(found);
  }

  // ── navigation ──────────────────────────────────────────────────────────
  protected go(page: number): void {
    const limit = this.pages();
    if (page >= 1 && page <= limit) this.page.set(page);
  }

  protected zoomBy(factor: number): void {
    this.zoom.update((z) => Math.min(4, Math.max(0.25, Math.round(z * factor * 20) / 20)));
  }

  protected fit(): void {
    this.zoom.set(1);
  }

  /** Jump to a finding's page, wherever it is in the set. */
  showFinding(finding: Finding): void {
    if (finding.page && finding.page !== this.page()) this.page.set(finding.page);
    this.findingPicked.emit(finding);
  }

  protected pickTool(tool: Tool): void {
    this.tool.set(tool);
    this.draft.set(null);
  }

  // ── drawing ─────────────────────────────────────────────────────────────
  private at(event: PointerEvent): { x: number; y: number } | null {
    const svg = this.overlayRef()?.nativeElement;
    const matrix = svg?.getScreenCTM();
    if (!svg || !matrix) return null;
    // getScreenCTM inverted is the exact screen-to-user-space map, including
    // whatever the page has done with transforms and scroll. Deriving it from
    // getBoundingClientRect by hand is the same arithmetic with more ways to be
    // subtly wrong.
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const mapped = point.matrixTransform(matrix.inverse());
    return { x: mapped.x, y: mapped.y };
  }

  protected onPointerDown(event: PointerEvent): void {
    const tool = this.tool();
    if (!this.canDraw() || tool === SELECT) return;
    const point = this.at(event);
    if (!point) return;

    (event.target as Element).setPointerCapture?.(event.pointerId);
    event.preventDefault();
    this.draft.set({
      kind: tool,
      x0: point.x,
      y0: point.y,
      x1: point.x,
      y1: point.y,
      points: [[point.x, point.y]],
    });
  }

  protected onPointerMove(event: PointerEvent): void {
    const current = this.draft();
    if (!current) return;
    const point = this.at(event);
    if (!point) return;
    this.draft.set({
      ...current,
      x1: point.x,
      y1: point.y,
      points:
        current.kind === 'freehand' ? [...current.points, [point.x, point.y]] : current.points,
    });
  }

  protected onPointerUp(): void {
    const current = this.draft();
    this.draft.set(null);
    if (!current) return;

    const width = Math.abs(current.x1 - current.x0);
    const height = Math.abs(current.y1 - current.y0);
    if (current.kind !== 'note' && width < MIN_DRAG && height < MIN_DRAG) return;

    const x0 = Math.min(current.x0, current.x1);
    const y0 = Math.min(current.y0, current.y1);
    const x1 = Math.max(current.x0, current.x1);
    const y1 = Math.max(current.y0, current.y1);

    this.markupDrawn.emit({
      page: this.page(),
      kind: current.kind,
      // An arrow points from where the drag started to where it ended, so its
      // geometry keeps the raw corners rather than the normalised box.
      geometry:
        current.kind === 'arrow'
          ? { x0: current.x0, y0: current.y0, x1: current.x1, y1: current.y1, points: [] }
          : current.kind === 'freehand'
            ? { x0, y0, x1, y1, points: current.points }
            : { x0, y0, x1, y1, points: [] },
      comment: '',
      colour: '',
      sheet: this.sheetLabel(),
      finding_fid: this.selectedFid(),
    });
  }

  /** The sheet number, taken from a finding already placed on this page. */
  private sheetLabel(): string {
    return this.findings().find((f) => f.page === this.page())?.sheet ?? '';
  }

  // ── overlay geometry helpers, used by the template ──────────────────────
  protected path(points: number[][]): string {
    return points.map(([x, y], i) => `${i ? 'L' : 'M'}${x} ${y}`).join(' ');
  }

  protected midY(box: { y0: number; y1: number }): number {
    return (box.y0 + box.y1) / 2;
  }

  /** Stroke widths are in PDF points, so they must not thin out as you zoom in. */
  protected readonly hairline = computed(() => 1.2 / this.zoom());
}
