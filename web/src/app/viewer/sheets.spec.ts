import { describe, expect, it } from 'vitest';

import {
  Finding,
  FindingSeverityEnum,
  FindingStatusEnum,
  Markup,
  SheetRef,
} from '../api/model/models';
import { sheetChips } from './sheets';

function sheet(page: number, code: string, read = true): SheetRef {
  return { page, code, title: '', discipline: '', read };
}

function finding(page: number, severity: FindingSeverityEnum, status = FindingStatusEnum.Open): Finding {
  return {
    fid: `F-${page}-${severity}`,
    page,
    severity,
    status,
    anchor: '',
    checked: '',
    code: '',
    discipline: '',
    result: '',
    rule_id: '',
    sheet: '',
    title: '',
  };
}

function base(over: Partial<Parameters<typeof sheetChips>[0]> = {}) {
  return sheetChips({
    pages: 3,
    sheets: [],
    findings: [],
    markups: [],
    comments: new Map(),
    ...over,
  });
}

describe('sheetChips', () => {
  it('names a sheet by its sheet number when one was read', () => {
    const chips = base({ sheets: [sheet(1, 'M.001'), sheet(2, 'M.002')] });
    expect(chips.map((c) => c.code)).toEqual(['M.001', 'M.002', 'Sheet 3']);
  });

  it('does not pass off a page number as a sheet number', () => {
    // `read: false` means the title block could not be read. Captioning the
    // chip "p2" would be the viewer claiming the drawing is called p2.
    const chips = base({ pages: 1, sheets: [sheet(1, 'p1', false)] });
    expect(chips[0].code).toBe('Sheet 1');
    expect(chips[0].read).toBe(false);
  });

  it('counts open findings and carries the worst severity to the chip', () => {
    const chips = base({
      findings: [
        finding(2, FindingSeverityEnum.Medium),
        finding(2, FindingSeverityEnum.Critical),
        finding(3, FindingSeverityEnum.Low),
      ],
    });
    expect(chips[1].findings).toBe(2);
    expect(chips[1].severity).toBe('CRITICAL');
    expect(chips[2].severity).toBe('LOW');
    expect(chips[0].severity).toBe('');
  });

  it('leaves passing checks out of the count', () => {
    // The rail is a map of where the work is. A sheet whose every check held
    // is not work, and colouring it as though it were would flatten the map.
    const chips = base({
      findings: [finding(1, FindingSeverityEnum.Verified, FindingStatusEnum.Pass)],
    });
    expect(chips[0].findings).toBe(0);
    expect(chips[0].severity).toBe('');
  });

  it('counts a conflict as work, because it is', () => {
    const chips = base({
      findings: [finding(1, FindingSeverityEnum.High, FindingStatusEnum.Conflict)],
    });
    expect(chips[0].findings).toBe(1);
  });

  it('counts reviewer markup and the file’s own comments per sheet', () => {
    const markup = { page: 3 } as Markup;
    const chips = base({ markups: [markup, markup], comments: new Map([[1, 4]]) });
    expect(chips[2].marks).toBe(2);
    expect(chips[0].comments).toBe(4);
  });

  it('covers every page of the document, not every sheet of the set', () => {
    // The marked-up PDF appends the findings register past the end of the set.
    // Those pages are real and worth reaching; they simply have no sheet number.
    const chips = sheetChips({
      pages: 5,
      sheets: [sheet(1, 'G-0')],
      findings: [],
      markups: [],
      comments: new Map(),
    });
    expect(chips).toHaveLength(5);
    expect(chips[4].code).toBe('Sheet 5');
  });

  it('is empty for a document with no pages yet', () => {
    expect(sheetChips({ pages: 0, sheets: [], findings: [], markups: [], comments: new Map() })).toEqual(
      [],
    );
  });
});
