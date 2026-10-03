/**
 * The marked-up DXF goes through the same signed-URL path as the other
 * downloads, and an absent one is no link at all.
 *
 * The server sends `markup_dxf` as an empty string for a PDF review, and for a
 * drawing whose DXF markup failed. `freshDownload` promises a URL or null, and
 * an empty string is neither: a caller that checks for null rather than for
 * truthiness would set `window.location.href = ''`, which reloads the page and
 * throws away the result on screen. So it returns null for one, on every path:
 * fresh, refreshed, and refresh-failed.
 */
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ConfigApi, Downloads, Job, JobStateEnum, ReviewsApi } from '../api';
import { AuthService } from '../core/auth';
import { ReviewService } from './review-service';

/** An hour out: comfortably inside the 30 s refresh margin. */
const LATER = () => new Date(Date.now() + 3_600_000).toISOString();
/** Already expired, so the next call must refresh. */
const EARLIER = () => new Date(Date.now() - 60_000).toISOString();

function done(downloads: Partial<Downloads>): Job {
  return {
    id: 'cad123',
    filename: 'EVERGREEN_BLDG_1.dwg',
    state: JobStateEnum.Done,
    stage: 6,
    stage_label: 'Delivering',
    stages: ['a', 'b', 'c', 'd', 'e', 'f', 'g'],
    options: {},
    bytes: 1024,
    created_at: new Date().toISOString(),
    elapsed_seconds: 296,
    downloads: {
      expires_at: LATER(),
      markup_pdf: 'https://storage.example/markup.pdf?sig=1',
      // Empty so opening the job does not go and fetch the findings.
      findings_json: '',
      ...downloads,
    },
  } as Job;
}

describe('ReviewService.freshDownload — the marked-up DXF', () => {
  let reviews: { getJob: ReturnType<typeof vi.fn> };
  let service: ReviewService;

  /** Put a finished job in the service the way the page does: by opening it. */
  function holding(job: Job): void {
    reviews.getJob.mockReturnValueOnce(of(job));
    service.open(job.id);
    expect(service.job()?.id).toBe(job.id);
  }

  beforeEach(() => {
    sessionStorage.clear();
    reviews = { getJob: vi.fn() };
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: ReviewsApi, useValue: reviews },
        { provide: ConfigApi, useValue: { getConfig: () => new Subject() } },
        { provide: AuthService, useValue: { markNotAllowed: () => {} } },
      ],
    });
    service = TestBed.inject(ReviewService);
  });

  afterEach(() => service.stop());

  it('returns the signed DXF link while it is still valid, without refetching', async () => {
    holding(done({ markup_dxf: 'https://storage.example/markup-dxf.zip?sig=1' }));
    reviews.getJob.mockClear();

    expect(await service.freshDownload('markup_dxf')).toBe(
      'https://storage.example/markup-dxf.zip?sig=1',
    );
    expect(reviews.getJob).not.toHaveBeenCalled();
  });

  it('refreshes an expired link and returns the newly signed one', async () => {
    holding(
      done({ expires_at: EARLIER(), markup_dxf: 'https://storage.example/markup-dxf.zip?sig=old' }),
    );
    reviews.getJob.mockReturnValueOnce(
      of(done({ markup_dxf: 'https://storage.example/markup-dxf.zip?sig=new' })),
    );

    expect(await service.freshDownload('markup_dxf')).toBe(
      'https://storage.example/markup-dxf.zip?sig=new',
    );
    expect(reviews.getJob).toHaveBeenLastCalledWith('cad123');
    // The refreshed job replaces the stale one, so the next click is free.
    expect(service.job()?.downloads?.markup_dxf).toContain('sig=new');
  });

  it('returns null, not an empty string, for a review with no DXF', async () => {
    holding(done({ markup_dxf: '' }));
    expect(await service.freshDownload('markup_dxf')).toBeNull();
  });

  it('returns null when the field is absent altogether, as from an older server', async () => {
    holding(done({}));
    expect(await service.freshDownload('markup_dxf')).toBeNull();
  });

  it('returns null when the refreshed job has no DXF either', async () => {
    holding(done({ expires_at: EARLIER(), markup_dxf: '' }));
    reviews.getJob.mockReturnValueOnce(of(done({ markup_dxf: '' })));
    expect(await service.freshDownload('markup_dxf')).toBeNull();
  });

  it('falls back to the link it had when the refresh fails', async () => {
    holding(
      done({ expires_at: EARLIER(), markup_dxf: 'https://storage.example/markup-dxf.zip?sig=old' }),
    );
    reviews.getJob.mockReturnValueOnce(throwError(() => new Error('offline')));
    expect(await service.freshDownload('markup_dxf')).toBe(
      'https://storage.example/markup-dxf.zip?sig=old',
    );
  });

  it('falls back to null, not an empty string, when the refresh fails with no DXF', async () => {
    holding(done({ expires_at: EARLIER(), markup_dxf: '' }));
    reviews.getJob.mockReturnValueOnce(throwError(() => new Error('offline')));
    expect(await service.freshDownload('markup_dxf')).toBeNull();
  });

  it('leaves the marked-up PDF exactly as it was', async () => {
    holding(done({ markup_dxf: '' }));
    expect(await service.freshDownload('markup_pdf')).toBe(
      'https://storage.example/markup.pdf?sig=1',
    );
  });

  it('returns null before any review has finished', async () => {
    expect(await service.freshDownload('markup_dxf')).toBeNull();
  });
});
