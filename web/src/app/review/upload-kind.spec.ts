/**
 * Which files the upload page offers, and which of them it reads for the
 * questionnaire.
 *
 * The two mistakes these catch are both silent. A regex that misses `.DXF` in
 * capitals turns a drawing away with a message that says it is not a drawing;
 * one that takes `set.pdf.exe` for a PDF sends it to the server to be refused
 * there, after the upload. And `isCad` is what keeps a DWG out of prefill,
 * which runs inside the API request and would convert the drawing there.
 */
import { describe, expect, it } from 'vitest';

import {
  ACCEPT,
  CAD_FILE,
  SET_FILE,
  acceptFor,
  isCad,
  projectName,
  uploadKind,
} from './upload-kind';

describe('uploadKind', () => {
  it('names each kind it accepts, whatever the case of the extension', () => {
    expect(uploadKind('set.pdf')).toBe('pdf');
    expect(uploadKind('SET.PDF')).toBe('pdf');
    expect(uploadKind('EVERGREEN_BLDG_1.dwg')).toBe('dwg');
    expect(uploadKind('A-101.DXF')).toBe('dxf');
    expect(uploadKind('sheets.zip')).toBe('zip');
  });

  it('refuses anything else, including a PDF name with something after it', () => {
    for (const name of ['model.rvt', 'plot.dwf', 'set.pdf.exe', 'noext', 'sheet.dwg.bak', '']) {
      expect(uploadKind(name), name).toBeNull();
    }
  });

  it('reads only the last extension', () => {
    // A drawing exported as `A-101.pdf.dwg` is a DWG, not a PDF.
    expect(uploadKind('A-101.pdf.dwg')).toBe('dwg');
  });
});

describe('isCad', () => {
  it('is true for a drawing and false for a PDF', () => {
    expect(isCad('EVERGREEN_BLDG_1.dwg')).toBe(true);
    expect(isCad('A-101.DXF')).toBe(true);
    expect(isCad('sheets.zip')).toBe(true);
    expect(isCad('set.pdf')).toBe(false);
    expect(isCad('SET.PDF')).toBe(false);
  });

  it('is false for a file that is not a set at all', () => {
    expect(isCad('model.rvt')).toBe(false);
    expect(isCad('noext')).toBe(false);
  });

  it('agrees with the exported patterns', () => {
    // Every CAD name is a set name; the page tests both and must not disagree.
    for (const name of ['a.dwg', 'b.DXF', 'c.Zip']) {
      expect(CAD_FILE.test(name)).toBe(true);
      expect(SET_FILE.test(name)).toBe(true);
    }
  });
});

describe('projectName', () => {
  it('takes the extension off, whichever kind it is', () => {
    expect(projectName('EVERGREEN_BLDG_1.dwg')).toBe('EVERGREEN_BLDG_1');
    expect(projectName('Sculpted Hot Pilates.PDF')).toBe('Sculpted Hot Pilates');
    expect(projectName('A-101.dxf')).toBe('A-101');
    expect(projectName('sheets.zip')).toBe('sheets');
  });

  it('leaves the rest of the name alone', () => {
    expect(projectName('set.v2.pdf')).toBe('set.v2');
  });
});

describe('ACCEPT and acceptFor', () => {
  it('offers the drawing kinds by extension and still offers a PDF', () => {
    const parts = ACCEPT.split(',');
    expect(parts).toContain('.pdf');
    expect(parts).toContain('application/pdf');
    expect(parts).toContain('.dwg');
    expect(parts).toContain('.dxf');
    expect(parts).toContain('.zip');
  });

  it('offers every kind when the deployment has not said what it admits', () => {
    expect(acceptFor(undefined)).toBe(ACCEPT);
    expect(acceptFor(null)).toBe(ACCEPT);
    expect(acceptFor([])).toBe(ACCEPT);
  });

  it('leaves DWG out of the picker where there is no converter', () => {
    const accept = acceptFor(['pdf', 'dxf', 'zip']);
    expect(accept).toBe('.pdf,application/pdf,.dxf,.zip');
    expect(accept).not.toContain('.dwg');
  });

  it('matches the full list when every kind is admitted', () => {
    expect(acceptFor(['pdf', 'dwg', 'dxf', 'zip'])).toBe(ACCEPT);
  });
});
