/**
 * Submitting a review, following it, and reading the result.
 *
 * State is signals; the polling loop is RxJS, which is the one place in this
 * app where it clearly earns its place — an interval that widens, stops on a
 * terminal state, backs off when the tab is hidden, and cancels on destroy is
 * exactly what the operators are for.
 */
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { DestroyRef, Injectable, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import {
  Observable,
  Subscription,
  catchError,
  defer,
  expand,
  switchMap,
  takeWhile,
  tap,
  throwError,
  timer,
} from 'rxjs';

import {
  ConfigApi,
  ConfigResponse,
  Finding,
  FindingsDocument,
  Job,
  ReviewOptions,
  ReviewsApi,
} from '../api';
import { AuthService } from '../core/auth';

export interface ApiFailure {
  code: string;
  message: string;
}

/** First ten polls are fast, then widen — most reviews finish inside ten. */
const FAST_POLL_MS = 1000;
const SLOW_POLL_MS = 3000;
const FAST_POLLS = 10;
/** A backgrounded tab has nobody watching the progress bar. */
const HIDDEN_POLL_MS = 5000;

@Injectable({ providedIn: 'root' })
export class ReviewService {
  private readonly reviews = inject(ReviewsApi);
  private readonly configApi = inject(ConfigApi);
  private readonly http = inject(HttpClient);
  private readonly auth = inject(AuthService);
  private readonly destroyRef = inject(DestroyRef);

  private readonly _config = signal<ConfigResponse | null>(null);
  private readonly _job = signal<Job | null>(null);
  private readonly _findings = signal<Finding[] | null>(null);
  private readonly _failure = signal<ApiFailure | null>(null);
  private readonly _submitting = signal(false);

  readonly config = this._config.asReadonly();
  readonly job = this._job.asReadonly();
  readonly findings = this._findings.asReadonly();
  readonly failure = this._failure.asReadonly();
  readonly submitting = this._submitting.asReadonly();

  readonly running = computed(() => {
    const state = this._job()?.state;
    return state === 'queued' || state === 'running';
  });

  private polling: Subscription | null = null;

  // ── config ──────────────────────────────────────────────────────────────
  loadConfig(): void {
    this.configApi
      .getConfig()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (config) => {
          this._config.set(config);
          this._failure.set(null);
        },
        error: (error) => this.handle(error),
      });
  }

  // ── submit ──────────────────────────────────────────────────────────────
  submit(file: File, options: ReviewOptions): void {
    this._failure.set(null);
    this._findings.set(null);
    this._job.set(null);
    this._submitting.set(true);

    this.reviews
      .createReview(file, JSON.stringify(options))
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (accepted) => {
          this._submitting.set(false);
          this.follow(accepted.id);
        },
        error: (error) => {
          this._submitting.set(false);
          this.handle(error);
        },
      });
  }

  // ── poll ────────────────────────────────────────────────────────────────
  private follow(id: string): void {
    this.stop();

    let tick = 0;
    const nextDelay = (): number => {
      const base = tick < FAST_POLLS ? FAST_POLL_MS : SLOW_POLL_MS;
      tick += 1;
      return document.hidden ? Math.max(base, HIDDEN_POLL_MS) : base;
    };

    this.polling = defer(() => this.reviews.getJob(id))
      .pipe(
        expand(() => timer(nextDelay()).pipe(switchMap(() => this.reviews.getJob(id)))),
        // `true` keeps the terminal emission — without it the finished job is
        // fetched and then dropped.
        takeWhile((job) => job.state === 'queued' || job.state === 'running', true),
        tap((job) => this._job.set(job)),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe({
        next: (job) => {
          if (job.state === 'done') this.loadFindings(job);
        },
        error: (error) => this.handle(error),
      });
  }

  /** Stops polling. Called on navigation away and before a new submission. */
  stop(): void {
    this.polling?.unsubscribe();
    this.polling = null;
  }

  // ── results ─────────────────────────────────────────────────────────────
  /**
   * Fetches findings.json straight from Cloud Storage.
   *
   * A V4 signed URL lasts an hour. If the page has been open longer than that,
   * GCS answers 403 — so refresh the job for a newly signed URL and try once
   * more, rather than showing a broken result.
   */
  loadFindings(job: Job): void {
    const url = job.downloads?.findings_json;
    if (!url) return;

    this.http
      .get<FindingsDocument>(url)
      .pipe(
        catchError((error: HttpErrorResponse) => {
          if (error.status !== 403 && error.status !== 400) {
            return throwError(() => error);
          }
          return this.reviews.getJob(job.id).pipe(
            tap((fresh) => this._job.set(fresh)),
            switchMap((fresh) => {
              const retry = fresh.downloads?.findings_json;
              if (!retry) return throwError(() => error);
              return this.http.get<FindingsDocument>(retry);
            }),
          );
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe({
        next: (document) => this._findings.set(document.findings),
        error: () =>
          this._failure.set({
            code: 'findings_unavailable',
            message:
              'The review finished, but its findings could not be loaded. The download links below still work.',
          }),
      });
  }

  /**
   * A download URL that is still valid, refreshing the job first if the signed
   * URL has expired. Returns null if the job is not finished.
   */
  async freshDownload(which: 'markup_pdf' | 'findings_json'): Promise<string | null> {
    const job = this._job();
    if (!job?.downloads) return null;

    const expires = Date.parse(job.downloads.expires_at);
    if (Number.isFinite(expires) && expires - Date.now() > 30_000) {
      return job.downloads[which];
    }

    try {
      const fresh = await new Promise<Job>((resolve, reject) =>
        this.reviews.getJob(job.id).subscribe({ next: resolve, error: reject }),
      );
      this._job.set(fresh);
      return fresh.downloads?.[which] ?? null;
    } catch {
      return job.downloads[which];
    }
  }

  reset(): void {
    this.stop();
    this._job.set(null);
    this._findings.set(null);
    this._failure.set(null);
  }

  // ── errors ──────────────────────────────────────────────────────────────
  private handle(error: unknown): void {
    const failure = toFailure(error);
    // A 403 from our own API means the token was genuine and the address is
    // not on the allowlist. That is its own UI state, not a generic error.
    if (error instanceof HttpErrorResponse && error.status === 403) {
      this.auth.markNotAllowed(failure.message);
    }
    this._failure.set(failure);
  }
}

export function toFailure(error: unknown): ApiFailure {
  if (error instanceof HttpErrorResponse) {
    const body = error.error as { error?: ApiFailure } | null;
    if (body?.error?.code && body.error.message) return body.error;
    if (error.status === 0) {
      return {
        code: 'offline',
        message: 'Could not reach the service. Check your connection and try again.',
      };
    }
    return { code: `http_${error.status}`, message: error.statusText || 'The request failed.' };
  }
  return { code: 'unknown', message: 'Something went wrong.' };
}
