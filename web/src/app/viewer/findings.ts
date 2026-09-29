/**
 * What the viewer needs from a `Finding` beyond what it says, in one place.
 *
 * **Page numbering.** The engine numbers pages from 0 — its renderer indexes
 * `doc[f.page]` — and the viewer numbers them from 1, as every PDF reader a
 * person uses does. Comparing the two directly searched every finding one sheet
 * late and could never show one on the cover sheet. `viewerPage` is the only
 * place the difference is allowed to live.
 *
 * **Identity.** `fid` is not unique within a review: two under-width doors are
 * two H-03s, and a declared-versus-drawn divergence is two findings with one
 * fid. Anything drawn, tracked, selected or looked up uses `findingKey`. What is
 * sent to the server about a finding (feedback, markup) still says `fid`,
 * because that is what the server matches on.
 *
 * **Placement.** `rect` is where the engine put the marker, in the same space
 * the overlay draws in. When a review carries one the viewer uses it and does
 * not search the text layer, which is what places an anchor that exists only
 * in text a raster rebuild recovered.
 */
import { Finding } from '../api/model/models';
import type { Box } from './anchor';

export function viewerPage(finding: Pick<Finding, 'page'>): number {
  return finding.page + 1;
}

export function findingKey(finding: Pick<Finding, 'fid' | 'key'>): string {
  return finding.key || finding.fid;
}

/** The engine's own placement: `[x0, y0, x1, y1]`, viewport space at scale 1. */
export function engineBox(finding: Pick<Finding, 'rect'>): Box | null {
  const r = finding.rect;
  if (!r || r.length !== 4 || !r.every((v) => Number.isFinite(v))) return null;
  return { x0: r[0], y0: r[1], x1: r[2], y1: r[3] };
}
