import { describe, expect, it } from 'vitest';

import { Finding } from '../api/model/models';
import { engineBox, findingKey, viewerPage } from './findings';
import { sheetChips } from './sheets';

function finding(over: Partial<Finding> = {}): Finding {
  return {
    fid: 'H-02',
    rule_id: 'EGRESS.COMMON_PATH',
    status: 'OPEN',
    severity: 'HIGH',
    discipline: 'Means of egress',
    page: 0,
    sheet: 'G-0',
    anchor: 'COMMON PATH OF TRAVEL',
    title: 't',
    checked: 'c',
    result: 'r',
    code: 'FBC-B 1006.2.1',
    ...over,
  } as Finding;
}

describe('viewerPage', () => {
  it('turns the engine’s 0-based page into the sheet the viewer shows', () => {
    // The cover sheet is page 0 to the engine and sheet 1 on screen. Compared
    // raw, a finding on it could never be shown.
    expect(viewerPage(finding({ page: 0 }))).toBe(1);
    expect(viewerPage(finding({ page: 16 }))).toBe(17);
  });

  it('puts a finding’s badge on its own sheet in the rail, not the next one', () => {
    const chips = sheetChips({
      pages: 3,
      sheets: [
        { page: 1, code: 'G-0', title: 'COVER', discipline: 'G', read: true },
        { page: 2, code: 'G-1', title: 'LIFE SAFETY', discipline: 'G', read: true },
      ],
      findings: [finding({ page: 0 })],
      markups: [],
      comments: new Map(),
    });
    expect(chips.find((c) => c.code === 'G-0')?.findings).toBe(1);
    expect(chips.find((c) => c.code === 'G-1')?.findings).toBe(0);
  });
});

describe('findingKey', () => {
  it('tells two findings with one fid apart', () => {
    const a = finding({ fid: 'H-03', key: 'H-03' });
    const b = finding({ fid: 'H-03', key: 'H-03~2' });
    expect(findingKey(a)).not.toBe(findingKey(b));
  });

  it('falls back to the fid on a review written before keys existed', () => {
    expect(findingKey(finding({ fid: 'M-04', key: undefined }))).toBe('M-04');
  });
});

describe('engineBox', () => {
  it('reads the engine’s placement', () => {
    expect(engineBox(finding({ rect: [10, 20, 30, 40] }))).toEqual({
      x0: 10,
      y0: 20,
      x1: 30,
      y1: 40,
    });
  });

  it('refuses anything that is not four numbers', () => {
    expect(engineBox(finding({ rect: null }))).toBeNull();
    expect(engineBox(finding({ rect: [1, 2, 3] }))).toBeNull();
    expect(engineBox(finding({ rect: [1, 2, 3, Number.NaN] }))).toBeNull();
  });
});
