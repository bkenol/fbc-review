/**
 * The sheet rail's contents: one chip per sheet, with what is on it.
 *
 * ## Why a sheet rail rather than Previous / Next
 *
 * "Sheet 3 of 15" is not what anybody working through a permit set says. They
 * say M.001, or A-2, and they go to it directly because they already know which
 * discipline they are checking. Paging through fifteen sheets to reach the one
 * with the door schedule on it is the interaction a paper set does not make you
 * perform, and the previous viewer made you perform it twice — once to find the
 * sheet and again to get back.
 *
 * So the rail names every sheet, says what the review found on each, and goes
 * there in one click. The counts are the other half: they are what turns the
 * rail from a list of names into a map of where the work is.
 *
 * ## Where the names come from
 *
 * `summary.sheet_index`, which the worker reads off the title block through the
 * engine. A review run before that was recorded has none, and a sheet whose
 * number could not be read is flagged rather than captioned with a page number
 * dressed up as a drawing name. Both fall back to "Sheet n", which is honest:
 * it is the viewer's own numbering and does not claim to be the set's.
 *
 * Pure, and tested as such — no pdf.js, no DOM.
 */
import { Finding, Markup, SheetRef } from '../api/model/models';
import { viewerPage } from './findings';

export interface SheetChip {
  /** 1-based, as the viewer numbers pages. */
  page: number;
  /** What to call it: the sheet number, or "Sheet n" when none was read. */
  code: string;
  title: string;
  /** False when `code` is the viewer's page numbering rather than the set's. */
  read: boolean;
  /** Findings still open on this sheet. */
  findings: number;
  /** The worst open severity here, for the chip's rule. Empty when none. */
  severity: string;
  /** Annotations the reviewer has drawn on this sheet. */
  marks: number;
  /** Comments already inside the uploaded file on this sheet. */
  comments: number;
}

/** Worst first. Mirrors the renderer's own ordering. */
const ORDER = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'VERIFIED', 'MEASURED'];

function worst(a: string, b: string): string {
  if (!a) return b;
  if (!b) return a;
  const ia = ORDER.indexOf(a);
  const ib = ORDER.indexOf(b);
  return (ia < 0 ? ORDER.length : ia) <= (ib < 0 ? ORDER.length : ib) ? a : b;
}

export interface SheetChipInput {
  pages: number;
  sheets: SheetRef[];
  findings: Finding[];
  markups: Markup[];
  /** In-file comment counts by page, as the viewer found them. */
  comments: Map<number, number>;
}

/**
 * One chip per page of the document, in page order.
 *
 * Driven by `pages` rather than by the sheet index, so the rail always covers
 * the document that is open. A marked-up PDF carries the findings register on
 * pages past the end of the set, and those pages are real and worth reaching —
 * they get a chip with no sheet number, which is what they are.
 */
export function sheetChips(input: SheetChipInput): SheetChip[] {
  const named = new Map<number, SheetRef>();
  for (const sheet of input.sheets ?? []) named.set(sheet.page, sheet);

  const open = new Map<number, number>();
  const severity = new Map<number, string>();
  for (const finding of input.findings ?? []) {
    if (finding.status === 'PASS') continue;
    // Findings count from 0, sheets and markup from 1.
    const page = viewerPage(finding);
    open.set(page, (open.get(page) ?? 0) + 1);
    severity.set(page, worst(severity.get(page) ?? '', finding.severity));
  }

  const marks = new Map<number, number>();
  for (const markup of input.markups ?? []) {
    marks.set(markup.page, (marks.get(markup.page) ?? 0) + 1);
  }

  const chips: SheetChip[] = [];
  for (let page = 1; page <= Math.max(0, input.pages); page += 1) {
    const sheet = named.get(page);
    const read = !!sheet?.read && !!sheet.code;
    chips.push({
      page,
      code: read ? sheet!.code : `Sheet ${page}`,
      title: sheet?.title ?? '',
      read,
      findings: open.get(page) ?? 0,
      severity: severity.get(page) ?? '',
      marks: marks.get(page) ?? 0,
      comments: input.comments?.get(page) ?? 0,
    });
  }
  return chips;
}
