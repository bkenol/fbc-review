/**
 * The sheet viewer: a permit set, everything marked on it, and what you draw.
 *
 * ## Three kinds of mark, and why they are separate layers
 *
 * A sheet in front of a reviewer carries marks from three different authors,
 * and confusing them is how a viewer becomes untrustworthy:
 *
 * 1. **The review's findings.** Boxed where the engine's own renderer boxes
 *    them — same anchor text, same `hit`, so the screen and the marked-up PDF
 *    put the same marker in the same place.
 * 2. **The file's own comments.** Revision clouds, sticky notes, callouts the
 *    engineer or the last plans examiner left inside the PDF. See `./annots`
 *    for why these were invisible before and how they are recovered.
 * 3. **Reviewer markup.** What you draw here, in training mode.
 *
 * Each has its own toggle, because the question "is this the review's mark or
 * the drafter's?" has to be answerable by turning one off.
 *
 * ## Why the source set, with the review's markup as an option
 *
 * The review also produces a marked-up PDF with every finding burnt into the
 * page. Rendering that by default would show each finding twice — once burnt
 * in and once interactive — and neither copy could be turned off. So the source
 * set is what opens, and the marked-up document is a layer you switch to when
 * you want the engine's own drawing of it, or its register, which lives on the
 * pages past the end of the set.
 *
 * ## One coordinate space, and it is the PDF's
 *
 * The SVG overlay's `viewBox` is the page's own viewport at scale 1, so
 * everything drawn on it — finding pins, in-file comments, markup, the shape
 * being dragged right now — is expressed in PDF points and the browser does the
 * scaling. Zoom changes the canvas resolution and the element's CSS size and
 * nothing else.
 *
 * That is why `MarkupGeometry` is stored in PDF points rather than pixels: a
 * markup recorded in device coordinates is in the wrong place at every zoom
 * level but the one it was drawn at, and on a 24x36 sheet where a door tag is a
 * few points across, "roughly right" is not right.
 *
 * ## Nothing is placed by guessing
 *
 * A finding whose anchor text is not in the page's text layer is listed and
 * said to be unplaced rather than drawn somewhere plausible. An annotation with
 * no readable rectangle is dropped. A marker on the wrong part of somebody's
 * drawing is worse than no marker.
 */
import {
  Component,
  ElementRef,
  computed,
  effect,
  input,
  output,
  signal,
  untracked,
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

import {
  Finding,
  FindingScenarioEnum,
  Markup,
  MarkupColourInfo,
  MarkupKind,
  MarkupKindInfo,
  MarkupRequest,
  SheetRef,
} from '../api';
import { AnchorItem, Box, locateAnchors } from './anchor';
import { engineBox, findingKey, viewerPage } from './findings';
import {
  PageAnnotation,
  RawAnnotation,
  ToViewport,
  captionLines,
  pathOf,
  readAnnotations,
} from './annots';
import { FitMode, MAX_ZOOM, MIN_ZOOM, fitZoom } from './fit';
import { SheetChip, sheetChips } from './sheets';

export type { FitMode } from './fit';
export type { PageAnnotation } from './annots';
export type { SheetChip } from './sheets';

// Same origin, copied out of the package by the build (see angular.json). The
// Hosting CSP is `default-src 'self'` with no CDN, so a worker from anywhere
// else is blocked with no visible error.
pdfjs.GlobalWorkerOptions.workerSrc = 'pdf.worker.min.mjs';

/** The select tool draws nothing; it picks what is already there. */
export const SELECT = 'select';

export type Tool = typeof SELECT | MarkupKind;

/** Which document is on screen: the set as uploaded, or the reviewed copy. */
export type DocLayer = 'source' | 'markup';

/** An in-file comment, with the sheet it is on. */
export interface SheetAnnotation extends PageAnnotation {
  page: number;
}

export interface PlacedFinding {
  finding: Finding;
  /** `findingKey(finding)`: what the overlay tracks and the focus looks up. */
  key: string;
  /** Null when the anchor text could not be found on the page. */
  box: Box | null;
}

/**
 * "Show me this one."
 *
 * `nonce` is what makes picking the same row twice re-centre it. Without it the
 * input would be unchanged on the second click and the effect would not fire —
 * which is exactly when you most want it to, because you have scrolled away.
 */
export interface FocusRequest {
  kind: 'finding' | 'markup' | 'annotation';
  id: string;
  page: number;
  nonce: number;
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

/** How long a focused mark stays lit after the viewer scrolls to it. */
const FLASH_MS = 2200;

@Component({
  selector: 'app-sheet-viewer',
  templateUrl: './sheet-viewer.html',
})
export class SheetViewer {
  /** Signed URL for the set as uploaded. */
  readonly src = input.required<string>();
  /** Signed URL for the reviewed copy, when there is one. */
  readonly markupSrc = input('');
  readonly findings = input<Finding[]>([]);
  readonly markups = input<Markup[]>([]);
  readonly markupKinds = input<MarkupKindInfo[]>([]);
  readonly markupColours = input<MarkupColourInfo[]>([]);
  /** The set's sheets, for the navigator. Empty falls back to page numbers. */
  readonly sheets = input<SheetRef[]>([]);
  /** Training mode. Without it the viewer reads and does not draw. */
  readonly canDraw = input(false);
  /** The selected finding's fid: what markup drawn now is filed against. */
  readonly selectedFid = input<string>('');
  /** The selected finding's key, for lighting it. Two findings can share a fid. */
  readonly selectedKey = input<string>('');
  readonly selectedMarkupId = input<string>('');
  readonly selectedAnnotId = input<string>('');
  /** Set by the panel when a row is picked. See `FocusRequest`. */
  readonly focus = input<FocusRequest | null>(null);

  readonly findingPicked = output<Finding>();
  readonly markupDrawn = output<MarkupRequest>();
  readonly markupPicked = output<Markup>();
  readonly annotationPicked = output<SheetAnnotation>();
  /** Every comment found inside the file, so the panel can register them. */
  readonly annotationsRead = output<SheetAnnotation[]>();

  private readonly canvasRef = viewChild<ElementRef<HTMLCanvasElement>>('canvas');
  private readonly overlayRef = viewChild<ElementRef<SVGSVGElement>>('overlay');
  private readonly stageRef = viewChild<ElementRef<HTMLElement>>('stage');
  private readonly sheetRef = viewChild<ElementRef<HTMLElement>>('sheetbox');
  private readonly railRef = viewChild<ElementRef<HTMLElement>>('rail');

  protected readonly page = signal(1);
  protected readonly pages = signal(0);
  protected readonly zoom = signal(1);
  protected readonly loading = signal(true);
  protected readonly rendering = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly tool = signal<Tool>(SELECT);
  protected readonly draft = signal<Draft | null>(null);
  protected readonly select = SELECT;
  /** Which colour new markup is drawn in. Empty means unclassified. */
  protected readonly colour = signal('');

  /**
   * Fit the width, not the whole sheet.
   *
   * A permit sheet is 24x36 or larger, and fitting all of it into a browser
   * window puts a schedule's row height at two or three pixels — legible as a
   * grey band and nothing else. Every reviewer's first act was to zoom in, so
   * the viewer starts where they were going: full width, scrolling down the
   * sheet, which is also how the paper copy is read. Fit sheet is one click
   * away and is what you want to see where you are, not what you want to work
   * in.
   */
  protected readonly fitMode = signal<FitMode>('width');

  /** Which document is being shown. */
  protected readonly doc = signal<DocLayer>('source');

  /** The layers. Each one answers "whose mark is this?" by being switchable. */
  protected readonly showFindings = signal(true);
  protected readonly showComments = signal(true);
  protected readonly showMarkup = signal(true);
  /** Whether comment text is drawn on the sheet or only in the panel. */
  protected readonly showCaptions = signal(true);

  /** The id currently lit by a focus request, if any. */
  protected readonly flash = signal('');
  private flashTimer: ReturnType<typeof setTimeout> | null = null;
  /** A focus request waiting for its page to finish rendering. */
  private readonly pending = signal<FocusRequest | null>(null);

  /** The stage box, in CSS pixels. Fed by a ResizeObserver. */
  private readonly stageBox = signal<{ width: number; height: number }>({
    width: 0,
    height: 0,
  });

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
  private renderToken = 0;
  private loadToken = 0;
  /** Anchor boxes are the same for the life of a page; the search is not free. */
  private readonly located = signal<Map<string, Box | null>>(new Map());
  /** The file's own comments, by page. Read once when the set is opened. */
  private readonly annots = signal<Map<number, SheetAnnotation[]>>(new Map());

  /** The URL actually being rendered, which follows the layer switch. */
  protected readonly activeSrc = computed(() =>
    this.doc() === 'markup' ? this.markupSrc() : this.src(),
  );

  protected readonly viewBox = computed(() => {
    const { width, height } = this.extent();
    return `0 0 ${width} ${height}`;
  });

  protected readonly cssWidth = computed(() => this.extent().width * this.zoom());
  protected readonly cssHeight = computed(() => this.extent().height * this.zoom());

  /**
   * Findings on the page being shown, with wherever they could be placed.
   *
   * A finding that only exists under the declared reading is left off the
   * drawing, because the renderer leaves it off too: the permit is issued
   * against what was submitted and the AHJ reviews the sheet, so a marker there
   * would attribute to the drawings something the drawings do not say. It is in
   * the register with its basis stated, which is where it belongs.
   */
  private readonly onPage = computed(() =>
    this.findings().filter(
      (f) => viewerPage(f) === this.page() && f.scenario !== FindingScenarioEnum.AsDeclared,
    ),
  );

  protected readonly placed = computed<PlacedFinding[]>(() => {
    const boxes = this.located();
    return this.onPage().map((finding) => {
      const key = findingKey(finding);
      return { finding, key, box: boxes.get(key) ?? null };
    });
  });

  /** Whether a placed finding is the selected one. */
  protected isSelected(item: PlacedFinding): boolean {
    const key = this.selectedKey();
    return key ? item.key === key : item.finding.fid === this.selectedFid();
  }

  protected readonly unplaced = computed(() => this.placed().filter((p) => !p.box).length);

  protected readonly pageMarkups = computed(() =>
    this.markups().filter((m) => m.page === this.page()),
  );

  protected readonly pageAnnots = computed<SheetAnnotation[]>(
    () => this.annots().get(this.page()) ?? [],
  );

  /** Whether the review's own markup is drawn into the page on screen. */
  protected readonly burntIn = computed(() => this.doc() === 'markup');

  /** In-file comment counts by page, for the rail. */
  private readonly commentCounts = computed(() => {
    const counts = new Map<number, number>();
    for (const [page, marks] of this.annots()) counts.set(page, marks.length);
    return counts;
  });

  protected readonly chips = computed<SheetChip[]>(() =>
    sheetChips({
      pages: this.pages(),
      sheets: this.sheets(),
      findings: this.findings(),
      markups: this.markups(),
      comments: this.commentCounts(),
    }),
  );

  protected readonly here = computed<SheetChip | null>(
    () => this.chips()[this.page() - 1] ?? null,
  );

  constructor() {
    // Reload when the URL or the chosen document changes. A signed URL is
    // refreshed when it expires, so this fires on more than a change of set.
    effect(() => {
      const url = this.activeSrc();
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

    // Follow the stage's size. A workspace whose side panel collapses at a
    // breakpoint changes the stage width without the window resizing, so a
    // window listener would miss it — and a sheet that stays at the old zoom
    // after the pane it sits in has halved is the exact thing this is for.
    effect((onCleanup) => {
      const stage = this.stageRef()?.nativeElement;
      if (!stage || typeof ResizeObserver === 'undefined') return;
      const observer = new ResizeObserver(([entry]) => {
        const box = entry.contentRect;
        this.stageBox.set({ width: box.width, height: box.height });
      });
      observer.observe(stage);
      this.stageBox.set({ width: stage.clientWidth, height: stage.clientHeight });
      onCleanup(() => observer.disconnect());
    });

    // Re-fit when the stage or the page geometry changes, but never when the
    // reviewer has taken the zoom into their own hands.
    effect(() => {
      const box = this.stageBox();
      const extent = this.extent();
      const mode = this.fitMode();
      if (mode === 'manual' || !box.width || !box.height) return;
      const target = fitZoom(mode, extent, box);
      if (target && Math.abs(target - this.zoom()) > 0.001) this.zoom.set(target);
    });

    // A focus request: go to the page first, and hold the request until the
    // page has been rendered and its marks located.
    effect(() => {
      const request = this.focus();
      if (!request) return;
      untracked(() => {
        if (request.page) this.go(request.page);
        this.pending.set(request);
      });
    });

    // …then centre on it. Reading `located`, `annots` and `pageMarkups` is what
    // makes this run again when the page finishes: the box does not exist until
    // the anchors have been searched for.
    effect(() => {
      const request = this.pending();
      if (!request) return;
      this.located();
      this.annots();
      this.pageMarkups();
      this.zoom();
      const busy = this.rendering();
      if (request.page && this.page() !== request.page) return;

      untracked(() => {
        const box = this.boxFor(request);
        if (box) {
          this.centre(box);
          this.light(request.id);
          this.pending.set(null);
          return;
        }
        // No box and nothing still being drawn: the mark cannot be placed on
        // this sheet. The page is right, which is as far as honesty goes, and
        // the panel says why it could not be pointed at.
        if (!busy) this.pending.set(null);
      });
    });

    // Keep the current sheet's chip in view as the page changes, however it
    // changed — a rail whose selected chip is off screen is a rail you have to
    // search before you can use.
    effect(() => {
      const page = this.page();
      const rail = this.railRef()?.nativeElement;
      if (!rail) return;
      const chip = rail.children[page - 1] as HTMLElement | undefined;
      chip?.scrollIntoView({ block: 'nearest', inline: 'center' });
    });
  }

  // ── loading ─────────────────────────────────────────────────────────────
  private async load(url: string): Promise<void> {
    const token = ++this.loadToken;
    this.loading.set(true);
    this.error.set(null);
    // Switching between the set and its reviewed copy should not send you back
    // to sheet 1: it is the same sheet you were reading, drawn differently.
    const wanted = this.page();
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
      const document = await this.loading_task.promise;
      if (token !== this.loadToken) return;
      this.document = document;
      this.pages.set(document.numPages);
      this.page.set(Math.min(Math.max(1, wanted), document.numPages));
      this.loading.set(false);
      await this.draw();
      // Only the uploaded set's own comments are registered. The reviewed copy
      // carries the engine's annotations too, and listing those as "already in
      // the file" would credit the drafter with the review's own marks.
      if (this.doc() === 'source') void this.readComments(document, token);
    } catch {
      if (token !== this.loadToken) return;
      this.document = null;
      this.loading.set(false);
      // Deliberately not the exception text: it can carry the signed URL, and
      // a signed URL in a visible error is a credential on screen.
      this.error.set('This set could not be opened for viewing. The downloads below still work.');
    }
  }

  /**
   * Read every page's annotations once, when the set is opened.
   *
   * Whole-document rather than per-page because the panel registers them: a
   * reviewer needs to know there are four comments on M-2 without visiting M-2
   * to find out. It is text and geometry off page objects with no rendering, so
   * a 35-sheet set costs a fraction of one page's draw, and it runs after the
   * first sheet is on screen rather than before it.
   */
  private async readComments(document: PDFDocumentProxy, token: number): Promise<void> {
    const found = new Map<number, SheetAnnotation[]>();
    try {
      for (let number = 1; number <= document.numPages; number += 1) {
        if (token !== this.loadToken) return;
        const page = await document.getPage(number);
        const raw = (await page.getAnnotations({ intent: 'display' })) as RawAnnotation[];
        if (!raw?.length) continue;
        const marks = readAnnotations(raw, this.mapper(page.getViewport({ scale: 1 })));
        if (marks.length) {
          found.set(number, marks.map((mark) => ({ ...mark, page: number })));
        }
      }
    } catch {
      // A file whose annotation array will not parse still renders, and a sheet
      // you can read with no comment list beside it is far better than an error
      // where the drawing should be.
    }
    if (token !== this.loadToken) return;
    this.annots.set(found);
    this.annotationsRead.emit([...found.values()].flat());
  }

  /** PDF user space to viewport space, which is what the overlay draws in. */
  private mapper(viewport: Viewport): ToViewport {
    return (x, y) => {
      // applyTransform writes into the point it is given and returns nothing.
      const point: number[] = [x, y];
      pdfjs.Util.applyTransform(point, viewport.transform);
      return [point[0], point[1]];
    };
  }

  private async draw(): Promise<void> {
    const canvas = this.canvasRef()?.nativeElement;
    if (!this.document || !canvas) return;

    const token = ++this.renderToken;
    this.rendering.set(true);
    try {
      const page = await this.document.getPage(this.page());
      if (token !== this.renderToken) return;

      const base = page.getViewport({ scale: 1 });
      this.extent.set({ width: base.width, height: base.height });

      // A sheet whose extent has just changed has not been fitted yet: the fit
      // effect runs after this and will set the zoom, which re-enters here.
      // Rendering now would paint a 36-inch sheet at 100% for one frame and
      // throw it away — on a large set that is a visible flash of the title
      // block at full size. Bail and let the second pass do the work.
      if (this.fitMode() !== 'manual') {
        const box = this.stageBox();
        const target = fitZoom(this.fitMode(), { width: base.width, height: base.height }, box);
        if (target && Math.abs(target - this.zoom()) > 0.001) return;
      }

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
      // A sheet that has just been drawn is not a sheet that could not be
      // drawn. Without this the banner from one failure outlives it — and
      // switching between the set and the reviewed copy cancels a render in
      // flight, which is a failure that has already fixed itself by the time
      // anybody could read about it.
      this.error.set(null);

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
   *
   * The finding's `hit` picks which occurrence, exactly as the engine's
   * renderer does. On a door schedule that lists the same leaf width four
   * times, always taking the first would put this marker on a different door
   * from the one in the marked-up PDF, and the two would be reporting the same
   * finding about different rows.
   */
  private async locateAnchors(
    page: PDFPageProxy,
    viewport: Viewport,
    token: number,
  ): Promise<void> {
    const wanted = this.onPage().filter((f) => f.anchor || engineBox(f));
    if (!wanted.length) {
      this.located.set(new Map());
      return;
    }

    // The engine's own placement, where the review carries one. The text
    // layer is only read for the findings that still need searching for.
    const found = new Map<string, Box | null>();
    const search: Finding[] = [];
    for (const finding of wanted) {
      const box = engineBox(finding);
      if (box) found.set(findingKey(finding), box);
      else search.push(finding);
    }
    if (!search.length) {
      if (token === this.renderToken) this.located.set(found);
      return;
    }

    const content = await page.getTextContent();
    if (token !== this.renderToken) return;

    const to = this.mapper(viewport);
    const items: AnchorItem[] = content.items
      .filter((item): item is TextItem => 'str' in item && !!item.str.trim())
      .map((item) => {
        const [x, y] = to(item.transform[4], item.transform[5]);
        // The transform's origin is the text baseline, so the box runs upward
        // from it in viewport space.
        return {
          text: item.str,
          box: { x0: x, y0: y - item.height, x1: x + item.width, y1: y },
        };
      });

    for (const finding of search) {
      const boxes = locateAnchors(items, finding.anchor);
      found.set(findingKey(finding), boxes[finding.hit ?? 0] ?? null);
    }

    if (token === this.renderToken) this.located.set(found);
  }

  // ── navigation ──────────────────────────────────────────────────────────
  protected go(page: number): void {
    const limit = this.pages();
    if (page >= 1 && page <= limit) this.page.set(page);
  }

  /**
   * Paging from the keyboard, once the sheet has focus.
   *
   * Left and right rather than only Page Up and Page Down because the rail is
   * horizontal and that is the direction the sheets are laid out in. Home and
   * End reach the cover sheet and the last sheet of a large set without
   * fourteen presses.
   */
  protected onKey(event: KeyboardEvent): void {
    const step: Record<string, number> = {
      ArrowLeft: -1,
      ArrowRight: 1,
      PageUp: -1,
      PageDown: 1,
    };
    if (event.key in step) {
      this.go(this.page() + step[event.key]);
    } else if (event.key === 'Home') {
      this.go(1);
    } else if (event.key === 'End') {
      this.go(this.pages());
    } else {
      return;
    }
    event.preventDefault();
  }

  protected zoomBy(factor: number): void {
    // Zooming by hand is how you say "stop fitting this for me".
    this.fitMode.set('manual');
    this.zoom.update((z) =>
      Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Math.round(z * factor * 100) / 100)),
    );
  }

  /** Hand the zoom back to the viewer. The effect does the arithmetic. */
  protected fitTo(mode: FitMode): void {
    this.fitMode.set(mode);
    if (mode === 'manual') return;
    const target = fitZoom(mode, this.extent(), this.stageBox());
    if (target) this.zoom.set(target);
  }

  /** 100%, and hold it there. Occasionally what you want to check a hairline. */
  protected actualSize(): void {
    this.fitMode.set('manual');
    this.zoom.set(1);
  }

  /** Switch between the set as uploaded and the reviewed copy. */
  protected showDoc(layer: DocLayer): void {
    if (layer === 'markup' && !this.markupSrc()) return;
    this.doc.set(layer);
  }

  /** Jump to a finding's page, wherever it is in the set — the cover sheet included. */
  showFinding(finding: Finding): void {
    const page = viewerPage(finding);
    if (page !== this.page()) this.page.set(page);
    this.findingPicked.emit(finding);
  }

  protected pickTool(tool: Tool): void {
    this.tool.set(tool);
    this.draft.set(null);
  }

  /** Clicking the active colour again clears it back to unclassified. */
  protected pickColour(key: string): void {
    this.colour.update((current) => (current === key ? '' : key));
  }

  /** The swatch for a colour key, or the default annotation ink. */
  protected swatch(key: string): string {
    return this.markupColours().find((c) => c.key === key)?.hex ?? '';
  }

  // ── focusing one mark ───────────────────────────────────────────────────
  /** Where the thing a focus request names is, if it is on this page at all. */
  private boxFor(request: FocusRequest): Box | null {
    if (request.kind === 'finding') {
      return this.located().get(request.id) ?? null;
    }
    if (request.kind === 'annotation') {
      return this.pageAnnots().find((a) => a.id === request.id)?.box ?? null;
    }
    const markup = this.pageMarkups().find((m) => m.id === request.id);
    if (!markup) return null;
    const g = markup.geometry;
    const x0 = Math.min(g.x0 ?? 0, g.x1 ?? g.x0 ?? 0);
    const y0 = Math.min(g.y0 ?? 0, g.y1 ?? g.y0 ?? 0);
    const x1 = Math.max(g.x0 ?? 0, g.x1 ?? g.x0 ?? 0);
    const y1 = Math.max(g.y0 ?? 0, g.y1 ?? g.y0 ?? 0);
    return { x0, y0, x1, y1 };
  }

  /**
   * Scroll the stage so a box in PDF points sits in the middle of it.
   *
   * Measured off the two elements rather than derived from `offsetLeft`,
   * because the sheet is centred by auto margins inside a scrolling stage and
   * its offset parent is whatever the page's layout makes it. Two bounding
   * rectangles and the current scroll are exact under any of that.
   */
  private centre(box: Box): void {
    const stage = this.stageRef()?.nativeElement;
    const sheet = this.sheetRef()?.nativeElement;
    if (!stage || !sheet) return;

    const zoom = this.zoom();
    const stageBox = stage.getBoundingClientRect();
    const sheetBox = sheet.getBoundingClientRect();
    const x = sheetBox.left - stageBox.left + stage.scrollLeft + ((box.x0 + box.x1) / 2) * zoom;
    const y = sheetBox.top - stageBox.top + stage.scrollTop + ((box.y0 + box.y1) / 2) * zoom;

    stage.scrollTo({
      left: x - stage.clientWidth / 2,
      top: y - stage.clientHeight / 2,
      behavior: 'smooth',
    });
  }

  /** Light a mark for long enough to find it, then stop shouting about it. */
  private light(id: string): void {
    if (this.flashTimer) clearTimeout(this.flashTimer);
    this.flash.set(id);
    this.flashTimer = setTimeout(() => this.flash.set(''), FLASH_MS);
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
    // A pin and a text label are placed by clicking; everything else is a
    // shape, and a shape with no extent is a slip of the mouse.
    const placed = current.kind === 'note' || current.kind === 'text';
    if (!placed && width < MIN_DRAG && height < MIN_DRAG) return;

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
      colour: this.colour(),
      sheet: this.sheetLabel(),
      finding_fid: this.selectedFid(),
    });
  }

  /**
   * The sheet number to record markup against.
   *
   * The sheet index first, because it covers every sheet whether or not the
   * review found anything on it. A finding already placed here is the fallback
   * for a review that predates the index.
   */
  private sheetLabel(): string {
    const chip = this.here();
    if (chip?.read) return chip.code;
    return this.findings().find((f) => viewerPage(f) === this.page())?.sheet ?? '';
  }

  // ── overlay geometry helpers, used by the template ──────────────────────
  protected path(points: number[][]): string {
    return points.map(([x, y], i) => `${i ? 'L' : 'M'}${x} ${y}`).join(' ');
  }

  protected shapePath(points: number[][], closed: boolean): string {
    return pathOf(points, closed);
  }

  protected caption(text: string): string[] {
    return captionLines(text);
  }

  protected midY(box: { y0: number; y1: number }): number {
    return (box.y0 + box.y1) / 2;
  }

  protected midX(box: { x0: number; x1: number }): number {
    return (box.x0 + box.x1) / 2;
  }

  /**
   * A revision cloud around a rectangle.
   *
   * Scallops of a fixed arc length walked round the perimeter, so a cloud round
   * a door tag and one round half a floor plan both read as clouds rather than
   * one reading as a circle. The radius is clamped so a very small rectangle
   * still gets a few bumps instead of one.
   */
  protected cloud(box: {
    x0?: number;
    y0?: number;
    x1?: number;
    y1?: number;
  }): string {
    // Optional on the wire, because the geometry model carries defaults. A
    // missing coordinate makes a zero-extent box, which returns no path rather
    // than a cloud in the corner of the sheet.
    const [ax, ay, bx, by] = [box.x0 ?? 0, box.y0 ?? 0, box.x1 ?? 0, box.y1 ?? 0];
    const x0 = Math.min(ax, bx);
    const y0 = Math.min(ay, by);
    const x1 = Math.max(ax, bx);
    const y1 = Math.max(ay, by);
    const width = x1 - x0;
    const height = y1 - y0;
    if (width <= 0 || height <= 0) return '';

    const radius = Math.max(3, Math.min(9, Math.min(width, height) / 4));
    const parts: string[] = [`M${x0} ${y0}`];

    // One side at a time, each divided into a whole number of scallops so the
    // corners land on a bump rather than mid-arc.
    const side = (
      length: number,
      step: (i: number) => [number, number],
      sweep: number,
    ): void => {
      const bumps = Math.max(1, Math.round(length / (radius * 2)));
      for (let i = 1; i <= bumps; i++) {
        const [x, y] = step(i / bumps);
        parts.push(`A${radius} ${radius} 0 0 ${sweep} ${x} ${y}`);
      }
    };

    side(width, (t) => [x0 + width * t, y0], 1);
    side(height, (t) => [x1, y0 + height * t], 1);
    side(width, (t) => [x1 - width * t, y1], 1);
    side(height, (t) => [x0, y1 - height * t], 1);
    return parts.join(' ') + ' Z';
  }

  /** Stroke widths are in PDF points, so they must not thin out as you zoom in. */
  protected readonly hairline = computed(() => 1.2 / this.zoom());
}
