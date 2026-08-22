/**
 * Losing sight of a job must not lose the job.
 *
 * The bug these cover, observed live: a review finished on the server in 15.3 s
 * while the browser sat on a frozen progress bar. The poll had errored, the
 * subscription ended, and the job signal kept its last `running` value forever —
 * so the UI claimed to be working on something that had been done for minutes,
 * and the finished result was unreachable because the job id lived only in
 * memory.
 */
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ConfigApi, Job, JobStateEnum, ReviewsApi } from '../api';
import { AuthService } from '../core/auth';
import { ReviewService } from './review-service';

const JOB_KEY = 'fbc.lastJob';

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: 'abc123',
    filename: 'set.pdf',
    state: JobStateEnum.Done,
    stage: 4,
    stage_label: 'Delivering',
    stages: ['a', 'b', 'c', 'd', 'e'],
    options: {},
    bytes: 1024,
    created_at: new Date().toISOString(),
    elapsed_seconds: 15.3,
    ...overrides,
  } as Job;
}

describe('ReviewService — surviving a lost connection', () => {
  let reviews: { getJob: ReturnType<typeof vi.fn>; createReview: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    sessionStorage.clear();
    reviews = { getJob: vi.fn(), createReview: vi.fn() };

    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: ReviewsApi, useValue: reviews },
        { provide: ConfigApi, useValue: { getConfig: () => new Subject() } },
        { provide: AuthService, useValue: { markNotAllowed: () => {} } },
      ],
    });
  });

  it('resumes a finished job after a reload', () => {
    // The id is all that survives; everything else must come back off the API.
    sessionStorage.setItem(JOB_KEY, 'abc123');
    reviews.getJob.mockReturnValue(of(job({ state: JobStateEnum.Done })));

    const service = TestBed.inject(ReviewService);
    service.resumeLastJob();

    expect(reviews.getJob).toHaveBeenCalledWith('abc123');
    expect(service.job()?.state).toBe('done');
    // A terminal job is not worth resuming twice.
    expect(sessionStorage.getItem(JOB_KEY)).toBeNull();
  });

  it('resumes and keeps following a job that is still running', () => {
    sessionStorage.setItem(JOB_KEY, 'abc123');
    reviews.getJob.mockReturnValue(of(job({ state: JobStateEnum.Running, stage: 2 })));

    const service = TestBed.inject(ReviewService);
    service.resumeLastJob();

    expect(service.job()?.state).toBe('running');
    // Still in flight, so the id stays for the next reload.
    expect(sessionStorage.getItem(JOB_KEY)).toBe('abc123');
    service.stop();
  });

  it('forgets a job the server no longer has', () => {
    sessionStorage.setItem(JOB_KEY, 'gone');
    reviews.getJob.mockReturnValue(throwError(() => new Error('404')));

    const service = TestBed.inject(ReviewService);
    service.resumeLastJob();

    expect(sessionStorage.getItem(JOB_KEY)).toBeNull();
    expect(service.job()).toBeNull();
  });

  it('does nothing when there is no remembered job', () => {
    const service = TestBed.inject(ReviewService);
    service.resumeLastJob();
    expect(reviews.getJob).not.toHaveBeenCalled();
  });

  it('reset clears the remembered job', () => {
    sessionStorage.setItem(JOB_KEY, 'abc123');
    const service = TestBed.inject(ReviewService);
    service.reset();
    expect(sessionStorage.getItem(JOB_KEY)).toBeNull();
    expect(service.lostContact()).toBe(false);
  });

  it('exposes lostContact so a stalled poll is visible rather than silent', () => {
    const service = TestBed.inject(ReviewService);
    // The signal exists and starts clean; the template shows a "lost sight of
    // it" notice rather than leaving the progress bar frozen with no reason.
    expect(service.lostContact()).toBe(false);
  });
});
