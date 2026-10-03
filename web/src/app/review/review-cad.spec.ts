/**
 * The upload page with a drawing in hand instead of a PDF.
 *
 * Each case is something that goes wrong quietly if it regresses:
 *
 * - **Prefill.** A DWG sent to `/api/prefill` is refused by the server; before
 *   that refusal existed it would have been converted inside the API request.
 *   The page must not ask, and must drop a previous PDF's suggestions.
 * - **Scanned sets.** There is nothing to rebuild in a plot made from the
 *   drawing. Offering the option, or sending it on, says otherwise.
 * - **The DXF download.** Offered when, and only when, the server wrote one.
 * - **The conversion stage.** Keyed by the worker's exact string; a missing key
 *   shows the longest station of the job with no explanation.
 * - **A sheet count that does not exist yet.** A drawing's page count is null
 *   until it has been plotted, and must not render as " · sheets".
 *
 * Stubs the service with plain signals rather than standing up the HTTP layer:
 * what is under test is what the page does with a job, not how it got one.
 */
import { signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ConfigResponse, Finding, HistoryEntry, Job, PrefillResponse } from '../api';
import { AuthService } from '../core/auth';
import { Review } from './review';
import { ApiFailure, ReviewService } from './review-service';

/** The worker's stage list for a drawing (`webapp.worker.stages_for(False, cad=True)`). */
const CAD_STAGES = [
  'Converting the drawing',
  'Reading the PDF',
  'Extracting schedules and code data',
  'Running rules',
  'Rendering the markup',
  'Delivering',
];

/** The page's view of `ReviewService`: the signals it reads and the calls it makes. */
class FakeReviews {
  readonly config = signal<ConfigResponse | null>(null);
  readonly job = signal<Job | null>(null);
  readonly findings = signal<Finding[] | null>(null);
  readonly failure = signal<ApiFailure | null>(null);
  readonly submitting = signal(false);
  readonly lostContact = signal(false);
  readonly prefill = signal<PrefillResponse | null>(null);
  readonly reading = signal(false);
  readonly history = signal<HistoryEntry[] | null>(null);
  readonly running = signal(false);

  readonly loadConfig = vi.fn();
  readonly resumeLastJob = vi.fn();
  readonly loadHistory = vi.fn();
  readonly readSet = vi.fn();
  readonly forgetPrefill = vi.fn();
  readonly submit = vi.fn();
  readonly stop = vi.fn();
  readonly reset = vi.fn();
  readonly open = vi.fn();
  readonly reconnect = vi.fn();
  readonly freshDownload = vi.fn(async () => null as string | null);

  /** Show a job the way the poll would, with `running` derived as the service does. */
  show(job: Job): void {
    this.job.set(job);
    this.running.set(job.state === 'queued' || job.state === 'running');
  }
}

/** Just enough of `/api/config` for the form to render with its questions. */
function config(overrides: Partial<ConfigResponse> = {}): ConfigResponse {
  return {
    calibration_knobs: [],
    declaration_fields: [],
    declaration_groups: [],
    declaration_unlockable: [],
    defaults: {},
    dispositions: [],
    editions: [],
    feedback_aspects: [],
    mail: {},
    markup_kinds: [],
    max_pages: 300,
    max_upload_mb: 120,
    occupancy_groups: [],
    retain_days: 30,
    rules: [],
    severities: [],
    stages: [],
    training: { enabled: false },
    ...overrides,
  } as unknown as ConfigResponse;
}

function cadJob(overrides: Partial<Job> = {}): Job {
  return {
    id: 'cad123',
    filename: 'EVERGREEN_BLDG_1.dwg',
    state: 'running',
    stage: 0,
    stage_label: CAD_STAGES[0],
    stages: CAD_STAGES,
    options: {},
    bytes: 23 * 1048576,
    created_at: new Date().toISOString(),
    elapsed_seconds: 41,
    pages: null,
    source_format: 'dwg',
    ...overrides,
  } as unknown as Job;
}

/**
 * A finished drawing review. Sheets, layers, viewports, attributes, dimensions
 * and seconds are what the real EVERGREEN run reported; the claim count, the
 * converter string and the warning are stand-ins.
 */
function finishedCadJob(downloads: Record<string, string>, overrides: Partial<Job> = {}): Job {
  return cadJob({
    state: 'done',
    stage: CAD_STAGES.length - 1,
    stage_label: 'Delivering',
    pages: 8,
    elapsed_seconds: 296,
    downloads: {
      expires_at: new Date(Date.now() + 3_600_000).toISOString(),
      markup_pdf: 'https://storage.example/markup.pdf',
      findings_json: 'https://storage.example/findings.json',
      source_pdf: 'https://storage.example/source.pdf',
      ...downloads,
    },
    cad: {
      format: 'dwg',
      drawings: 1,
      sheets: 8,
      sheets_identified: 7,
      layers: 71,
      viewports: 53,
      attributes: 151,
      dimensions: 110,
      claims: 3,
      converter: 'dwg2dxf 0.14',
      seconds: 186.4,
      warnings: ['The title block refers to an xref that was not in the upload.'],
    },
    summary: { counts: { CRITICAL: 1 }, open: 1, sheets: 8, pages: 9, abstentions: [] },
    ...overrides,
  } as unknown as Job);
}

describe('Review — a drawing instead of a PDF', () => {
  let fixture: ComponentFixture<Review>;
  let reviews: FakeReviews;
  let page: HTMLElement;

  function build(): void {
    reviews = new FakeReviews();
    TestBed.configureTestingModule({
      imports: [Review],
      providers: [
        provideRouter([]),
        { provide: ReviewService, useValue: reviews },
        {
          provide: AuthService,
          useValue: {
            state: () => 'allowed',
            denied: () => null,
            email: () => 'reviewer@example.com',
            signOut: async () => {},
          },
        },
      ],
    });
    fixture = TestBed.createComponent(Review);
    fixture.detectChanges();
    page = fixture.nativeElement as HTMLElement;
  }

  /** Choose a file through the real input, as the native picker would. */
  function choose(file: File): void {
    const input = page.querySelector<HTMLInputElement>('input[type="file"]')!;
    Object.defineProperty(input, 'files', { value: [file], configurable: true });
    input.dispatchEvent(new Event('change'));
    fixture.detectChanges();
  }

  /** Drop files on the drop zone. jsdom has no DataTransfer, so the event is shaped by hand. */
  function drop(files: File[]): void {
    const zone = page.querySelector<HTMLElement>('label.drop')!;
    const event = new Event('drop', { cancelable: true }) as Event & {
      dataTransfer: { files: File[] };
    };
    Object.defineProperty(event, 'dataTransfer', { value: { files } });
    zone.dispatchEvent(event);
    fixture.detectChanges();
  }

  function text(): string {
    return (page.textContent ?? '').replace(/\s+/g, ' ');
  }

  function alert(): string {
    return (page.querySelector('.note.bad')?.textContent ?? '').replace(/\s+/g, ' ').trim();
  }

  function button(label: string): HTMLButtonElement | undefined {
    return Array.from(page.querySelectorAll('button')).find(
      (b) => (b.textContent ?? '').trim() === label,
    );
  }

  beforeEach(() => {
    TestBed.resetTestingModule();
    build();
  });

  // ── choosing the file ───────────────────────────────────────────────────
  it('accepts a DWG without reading it for the questionnaire', () => {
    choose(new File(['AC1032'], 'EVERGREEN_BLDG_1.dwg'));

    expect(alert()).toBe('');
    expect(reviews.readSet).not.toHaveBeenCalled();
    // A PDF chosen before it must not leave its suggestions behind.
    expect(reviews.forgetPrefill).toHaveBeenCalled();
    expect(page.querySelector<HTMLInputElement>('#project_name')!.value).toBe('EVERGREEN_BLDG_1');
    expect(text()).toContain('EVERGREEN_BLDG_1.dwg');
  });

  it('still reads a PDF for the questionnaire', () => {
    const file = new File(['%PDF-1.7'], 'set.pdf');
    choose(file);

    expect(alert()).toBe('');
    expect(reviews.readSet).toHaveBeenCalledWith(file);
    expect(page.querySelector<HTMLInputElement>('#project_name')!.value).toBe('set');
  });

  it('accepts a DXF and a zip as drawings too', () => {
    choose(new File(['0\nSECTION'], 'A-101.DXF'));
    expect(alert()).toBe('');
    choose(new File(['PK'], 'sheets.zip'));
    expect(alert()).toBe('');
    expect(reviews.readSet).not.toHaveBeenCalled();
  });

  it('turns away a file it cannot review, naming what it can', () => {
    choose(new File(['x'], 'model.rvt'));

    expect(alert()).toContain('That is not a file this can review.');
    expect(alert()).toContain('DWG, DXF, or a ZIP of DWG/DXF sheets');
    expect(reviews.readSet).not.toHaveBeenCalled();
  });

  it('asks for a zip when several loose sheets are dropped at once', () => {
    drop([new File(['0'], 'A-101.dxf'), new File(['0'], 'A-102.dxf')]);

    expect(alert()).toContain('That is 2 files.');
    expect(alert()).toContain('zip');
    expect(reviews.readSet).not.toHaveBeenCalled();
    expect(text()).not.toContain('A-101.dxf ·');
  });

  it('takes a single dropped drawing like a chosen one', () => {
    drop([new File(['AC1032'], 'EVERGREEN_BLDG_1.dwg')]);
    expect(alert()).toBe('');
    expect(text()).toContain('EVERGREEN_BLDG_1.dwg');
  });

  it('steers an oversized DXF to the DWG or a zip', () => {
    reviews.config.set(config({ max_upload_mb: 1 }));
    fixture.detectChanges();
    choose(new File([new Uint8Array(2 * 1048576)], 'EVERGREEN_BLDG_1.dxf'));

    expect(alert()).toContain('over the 1 MB limit');
    expect(alert()).toContain('upload the DWG, or zip the DXF');
  });

  it('says so before uploading a DWG where there is no converter', () => {
    reviews.config.set(config({ accepted_formats: ['pdf', 'dxf', 'zip'] as never }));
    fixture.detectChanges();

    const input = page.querySelector<HTMLInputElement>('input[type="file"]')!;
    expect(input.getAttribute('accept')).toBe('.pdf,application/pdf,.dxf,.zip');
    expect(text()).toContain('This deployment has no DWG converter.');

    choose(new File(['AC1032'], 'EVERGREEN_BLDG_1.dwg'));
    expect(alert()).toContain('no DWG converter installed');
  });

  it('offers every kind in the picker by default', () => {
    const input = page.querySelector<HTMLInputElement>('input[type="file"]')!;
    expect(input.getAttribute('accept')).toBe('.pdf,application/pdf,.dwg,.dxf,.zip');
    expect(text()).toContain('DWG, DXF, or a ZIP of DWG/DXF sheets');
  });

  // ── the form ────────────────────────────────────────────────────────────
  it('hides the scanned-sets option for a drawing and shows it for a PDF', () => {
    expect(page.querySelector('#convert_raster')).not.toBeNull();

    choose(new File(['AC1032'], 'EVERGREEN_BLDG_1.dwg'));
    expect(page.querySelector('#convert_raster')).toBeNull();
    expect(text()).not.toContain('Scanned sets');

    choose(new File(['%PDF-1.7'], 'set.pdf'));
    expect(page.querySelector('#convert_raster')).not.toBeNull();
  });

  it('says the conversion takes minutes once a drawing is chosen', () => {
    expect(text()).toContain('Choose a set to begin.');
    choose(new File(['AC1032'], 'EVERGREEN_BLDG_1.dwg'));
    expect(text()).toContain('The drawing is converted to sheets first');
    expect(text()).not.toContain('Typically 20–40 seconds');
  });

  it('never sends the scanned-sheet rebuild with a drawing', () => {
    choose(new File(['%PDF-1.7'], 'set.pdf'));
    const box = page.querySelector<HTMLInputElement>('#convert_raster')!;
    box.checked = true;
    box.dispatchEvent(new Event('change'));
    fixture.detectChanges();

    choose(new File(['AC1032'], 'EVERGREEN_BLDG_1.dwg'));
    page.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));

    expect(reviews.submit).toHaveBeenCalledTimes(1);
    const [file, options] = reviews.submit.mock.calls[0];
    expect((file as File).name).toBe('EVERGREEN_BLDG_1.dwg');
    expect(options.convert_raster).toBe(false);
  });

  it('still sends the rebuild for a PDF when it is ticked', () => {
    choose(new File(['%PDF-1.7'], 'set.pdf'));
    const box = page.querySelector<HTMLInputElement>('#convert_raster')!;
    box.checked = true;
    box.dispatchEvent(new Event('change'));
    page.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));

    expect(reviews.submit.mock.calls[0][1].convert_raster).toBe(true);
  });

  // ── while it runs ───────────────────────────────────────────────────────
  it('explains the conversion stage while the drawing is being converted', () => {
    reviews.show(cadJob());
    fixture.detectChanges();

    const station = page.querySelector('ol.stages li.on');
    expect(station?.querySelector('.name')?.textContent?.trim()).toBe('Converting the drawing');
    expect(station?.querySelector('.detail')?.textContent).toContain('converted to DXF');
  });

  it('leaves out a sheet count that does not exist yet', () => {
    reviews.show(cadJob({ pages: null }));
    fixture.detectChanges();

    const figures = (page.querySelector('p.figures')?.textContent ?? '').replace(/\s+/g, ' ');
    expect(figures).toContain('EVERGREEN_BLDG_1.dwg ·');
    expect(figures).toContain('41 s elapsed');
    expect(figures).not.toMatch(/·\s*sheets?\b/);
    expect(figures).not.toContain('null');
  });

  it('shows the count once the drawing has been plotted', () => {
    reviews.show(cadJob({ pages: 8, stage: 1, stage_label: 'Reading the PDF' }));
    fixture.detectChanges();

    const figures = (page.querySelector('p.figures')?.textContent ?? '').replace(/\s+/g, ' ');
    expect(figures).toContain('8 sheets ·');
  });

  // ── the result ──────────────────────────────────────────────────────────
  it('offers the marked-up DXF when the server wrote one', async () => {
    reviews.show(finishedCadJob({ markup_dxf: 'https://storage.example/markup-dxf.zip' }));
    fixture.detectChanges();

    const dxf = button('Marked-up DXF');
    expect(dxf).toBeDefined();
    dxf!.click();
    await fixture.whenStable();
    expect(reviews.freshDownload).toHaveBeenCalledWith('markup_dxf');
  });

  it('offers no DXF when there is none, and says the PDF stands', () => {
    reviews.show(finishedCadJob({ markup_dxf: '' }));
    fixture.detectChanges();

    expect(button('Marked-up DXF')).toBeUndefined();
    expect(button('Download marked-up set')).toBeDefined();
    expect(text()).toContain('The marked-up DXF could not be written for this review');
  });

  it('offers no DXF on a PDF review', () => {
    reviews.show(
      finishedCadJob(
        { markup_dxf: '' },
        { filename: 'set.pdf', source_format: 'pdf' as never, cad: null },
      ),
    );
    fixture.detectChanges();

    expect(button('Marked-up DXF')).toBeUndefined();
    expect(text()).not.toContain('Reviewed from the drawing');
    expect(text()).not.toContain('marked-up DXF could not be written');
  });

  it('says the review was made from the drawing, in counts, with what it could not read', () => {
    reviews.show(finishedCadJob({ markup_dxf: 'https://storage.example/markup-dxf.zip' }));
    fixture.detectChanges();

    const note = Array.from(page.querySelectorAll('.note')).find((n) =>
      (n.textContent ?? '').includes('Reviewed from the drawing, not a plot.'),
    );
    expect(note).toBeDefined();
    const said = (note!.textContent ?? '').replace(/\s+/g, ' ');
    expect(said).toContain(
      '8 sheets plotted in 186 s from EVERGREEN_BLDG_1.dwg, converted by dwg2dxf 0.14.',
    );
    expect(said).toContain('7 of them numbered from a title-block field');
    expect(said).toContain('71 CAD layers kept');
    expect(said).toContain('53 viewports');
    expect(said).toContain('3 values taken from the drawing');
    expect(said).toContain('xref that was not in the upload');
    // A warning makes it a warning note, like the rebuild's.
    expect(note!.classList.contains('warn')).toBe(true);
  });

  /** The CAD note's text, whitespace collapsed. */
  function cadNote(): string {
    const note = Array.from(page.querySelectorAll('.note')).find((n) =>
      (n.textContent ?? '').includes('Reviewed from the drawing, not a plot.'),
    );
    return (note?.textContent ?? '').replace(/\s+/g, ' ');
  }

  it('names no converter for a DXF, which is read as uploaded', () => {
    const job = finishedCadJob({ markup_dxf: 'https://storage.example/markup-dxf.zip' });
    reviews.show({
      ...job,
      filename: 'A-101.dxf',
      cad: { ...job.cad!, format: 'dxf' as never, sheets: 1, warnings: [] },
    });
    fixture.detectChanges();

    expect(cadNote()).toContain('1 sheet plotted in 186 s from A-101.dxf.');
    expect(cadNote()).not.toContain('converted by');
  });

  it('counts the drawings in a zip without claiming any of them was a DWG', () => {
    const job = finishedCadJob({ markup_dxf: 'https://storage.example/markup-dxf.zip' });
    reviews.show({
      ...job,
      filename: 'sheets.zip',
      cad: { ...job.cad!, format: 'zip' as never, drawings: 3, warnings: [] },
    });
    fixture.detectChanges();

    expect(cadNote()).toContain(
      'from sheets.zip (3 drawings), any DWG in it converted by dwg2dxf 0.14.',
    );
    expect(cadNote()).not.toMatch(/,\s+\./);
  });

  it('does not offer to rebuild scanned sheets on a drawing review', () => {
    reviews.show(
      finishedCadJob(
        { markup_dxf: 'https://storage.example/markup-dxf.zip' },
        { source: { region_pages: [3] } as never },
      ),
    );
    fixture.detectChanges();

    // The note is still true and still shown; the remedy is not one.
    expect(text()).toContain('paste part of the drawing in as an image');
    expect(button('Read those sheets and review again')).toBeUndefined();
  });
});
