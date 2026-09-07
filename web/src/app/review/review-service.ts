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
  retry,
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
  HistoryEntry,
  Job,
  PrefillResponse,
  ProjectDeclaration,
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
/** Consecutive failed polls tolerated before admitting we lost the job. */
const POLL_RETRIES = 5;

/** Survives a reload, so a finished job is not stranded on the server. */
const JOB_KEY = 'fbc.lastJob';

function rememberJob(id: string): void {
  try {
    sessionStorage.setItem(JOB_KEY, id);
  } catch {
    /* private mode, storage disabled — resuming is a convenience, not a need */
  }
}

function rememberedJob(): string | null {
  try {
    return sessionStorage.getItem(JOB_KEY);
  } catch {
    return null;
  }
}

function forgetJob(): void {
  try {
    sessionStorage.removeItem(JOB_KEY);
  } catch {
    /* as above */
  }
}

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
  private readonly _rerunning = signal(false);
  private readonly _lostContact = signal(false);
  private readonly _prefill = signal<PrefillResponse | null>(null);
  private readonly _reading = signal(false);
  private readonly _history = signal<HistoryEntry[] | null>(null);

  readonly config = this._config.asReadonly();
  readonly job = this._job.asReadonly();
  readonly findings = this._findings.asReadonly();
  readonly failure = this._failure.asReadonly();
  readonly submitting = this._submitting.asReadonly();
  /** True while a re-run of a finished review is being accepted. */
  readonly rerunning = this._rerunning.asReadonly();
  /** True when polling is failing. The job itself is probably fine. */
  readonly lostContact = this._lostContact.asReadonly();
  /** What the chosen set states about itself, for the declaration to offer. */
  readonly prefill = this._prefill.asReadonly();
  /** True while the set is being read for those answers. */
  readonly reading = this._reading.asReadonly();
  /** Past reviews, newest first. Null until asked for. */
  readonly history = this._history.asReadonly();

  readonly running = computed(() => {
    const state = this._job()?.state;
    return state === 'queued' || state === 'running';
  });

  private polling: Subscription | null = null;

  // ── config ──────────────────────────────────────────────────────────────
  /**
   * `onLoaded` runs once, on the first successful fetch. Callers use it to seed
   * a form from the server's defaults — which must not happen again on a later
   * refetch, or it overwrites what the person has since typed.
   */
  loadConfig(onLoaded?: (config: ConfigResponse) => void): void {
    let seeded = false;
    this.configApi
      .getConfig()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (config) => {
          this._config.set(config);
          this._failure.set(null);
          if (!seeded) {
            seeded = true;
            onLoaded?.(config);
          }
        },
        error: (error) => this.handle(error),
      });
  }

  /**
   * The config, fetched once, as a promise.
   *
   * For the route guards, which have to decide before a screen is rendered and
   * cannot read a signal that has not been filled in yet. Everything else in
   * the app reads `config()` and re-renders when it arrives.
   */
  whenConfigured(): Promise<ConfigResponse | null> {
    const existing = this._config();
    if (existing) return Promise.resolve(existing);
    return new Promise((resolve) => {
      this.configApi.getConfig().subscribe({
        next: (config) => {
          this._config.set(config);
          resolve(config);
        },
        // A guard must not hang on an unreachable API. Resolving null denies
        // the owner route, which is the safe direction to fail in.
        error: () => resolve(null),
      });
    });
  }

  // ── read the set before asking about it ─────────────────────────────────
  /**
   * Parse the chosen set and pull out what it already states, so the
   * questionnaire opens with those answers filled in rather than blank.
   *
   * Failure here is deliberately quiet. Prefill is a convenience: if the set
   * cannot be read, or the request fails, the questionnaire simply opens empty,
   * which is exactly how it worked before. Surfacing an error for a step nobody
   * asked for would be noise — the review's own upload will report anything
   * that genuinely matters about the file.
   */
  readSet(file: File): void {
    this._prefill.set(null);
    this._reading.set(true);
    this.reviews
      .prefillDeclaration(file)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (found) => {
          this._prefill.set(found);
          this._reading.set(false);
        },
        error: () => {
          this._prefill.set(null);
          this._reading.set(false);
        },
      });
  }

  /**
   * Re-open a past review by id.
   *
   * Nothing is re-run and nothing is re-uploaded: the job record is still in
   * Firestore and its findings.json is still in Cloud Storage, so this is the
   * same fetch the live review does when it finishes. A review that never
   * finished is shown in whatever state it ended in, error and all.
   */
  open(id: string): void {
    this.stop();
    this._failure.set(null);
    this._findings.set(null);
    // Cleared before the fetch, not after it succeeds. Opening a second review
    // and having the fetch fail used to leave the *first* one on screen —
    // header, findings, abstentions and all — under the second one's URL, which
    // is a stale register somebody has every reason to believe. Reaching that
    // took a lucky failure until re-running a review made it one click away.
    this._job.set(null);
    this._lostContact.set(false);
    this._prefill.set(null);
    this.reviews.getJob(id).subscribe({
      next: (job) => {
        this._job.set(job);
        if (job.state === 'done') this.loadFindings(job);
        else if (job.state !== 'error') this.follow(id);
      },
      error: (error) => this.handle(error),
    });
  }

  forgetPrefill(): void {
    this._prefill.set(null);
    this._reading.set(false);
  }

  // ── history ─────────────────────────────────────────────────────────────
  /**
   * Past reviews. The findings register is the deliverable, and re-running a
   * 24-sheet set to look at one again is thirty seconds and a second copy of
   * the same PDF. Nothing new is stored for this — the marked-up set and
   * findings.json are already in Cloud Storage.
   */
  loadHistory(): void {
    this.reviews
      .listJobs()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (page) => this._history.set(page.entries),
        // An unreachable history must never take the upload form down with it.
        error: () => this._history.set([]),
      });
  }

  // ── submit ──────────────────────────────────────────────────────────────
  /**
   * One continuous submit: the questionnaire and the file go up together.
   *
   * The set is read first, by `readSet`, purely to fill the questionnaire in —
   * that is a separate, discardable request and not a phase of the job. The
   * review itself is still one call, and what the applicant submits is what
   * gets declared.
   */
  submit(file: File, options: ReviewOptions, declaration: ProjectDeclaration = {}): void {
    this._failure.set(null);
    this._findings.set(null);
    this._job.set(null);
    this._submitting.set(true);

    this.reviews
      .createReview(file, JSON.stringify(declaration), JSON.stringify(options))
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

  // ── re-run ──────────────────────────────────────────────────────────────
  /**
   * Review the same set again with more of the declaration answered.
   *
   * The set is not re-sent: the PDF is in the bucket under the first review's
   * id and the server replays that review's options and admission profile, so
   * this costs one pass of the engine. What comes back is a *new* review with
   * its own id — the first one is not edited, because a review is a dated
   * statement about a set under stated assertions and rewriting one would
   * change what somebody was already told.
   *
   * `onStarted` receives the new id. The caller navigates; this service does
   * not, because it has no router and a service that navigates is a service you
   * cannot call from anywhere else.
   */
  rerun(id: string, declaration: ProjectDeclaration, onStarted: (id: string) => void): void {
    this._failure.set(null);
    this._rerunning.set(true);

    this.reviews
      .rerunReview(id, { declaration })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (accepted) => {
          this._rerunning.set(false);
          onStarted(accepted.id);
        },
        error: (error) => {
          this._rerunning.set(false);
          this.handle(error);
        },
      });
  }

  // ── poll ────────────────────────────────────────────────────────────────
  /**
   * Follow a job to a terminal state.
   *
   * The review runs on the server and does not care whether we are watching.
   * So a dropped poll is a failure of *our view*, not of the job, and it must
   * not end the run — otherwise a single blip leaves the progress bar frozen
   * partway while the review quietly finishes without us. Observed exactly
   * that: the UI stuck at 11.5 s on a job that completed at 15.3 s.
   */
  private follow(id: string): void {
    this.stop();
    this._lostContact.set(false);
    rememberJob(id);

    let tick = 0;
    const nextDelay = (): number => {
      const base = tick < FAST_POLLS ? FAST_POLL_MS : SLOW_POLL_MS;
      tick += 1;
      return document.hidden ? Math.max(base, HIDDEN_POLL_MS) : base;
    };

    this.polling = defer(() => this.reviews.getJob(id))
      .pipe(
        expand(() => timer(nextDelay()).pipe(switchMap(() => this.reviews.getJob(id)))),
        // Resubscribes the whole poll loop rather than giving up, backing off
        // each time. Only after several consecutive failures do we admit we
        // have lost track of it.
        retry({
          count: POLL_RETRIES,
          delay: (_error, attempt) => {
            this._lostContact.set(true);
            return timer(Math.min(1000 * 2 ** attempt, 8000));
          },
        }),
        tap(() => this._lostContact.set(false)),
        // `true` keeps the terminal emission — without it the finished job is
        // fetched and then dropped.
        takeWhile((job) => job.state === 'queued' || job.state === 'running', true),
        tap((job) => this._job.set(job)),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe({
        next: (job) => {
          if (job.state === 'done') this.loadFindings(job);
          if (job.state === 'done' || job.state === 'error') forgetJob();
        },
        error: (error) => {
          // Out of retries. Say so against the job that is still running,
          // rather than silently freezing the progress bar.
          this._lostContact.set(true);
          this.handle(error);
        },
      });
  }

  /**
   * Resume watching the job this browser last submitted.
   *
   * Without this a reload loses the job id and the result becomes unreachable
   * even though it is sitting finished on the server.
   */
  resumeLastJob(): void {
    const id = rememberedJob();
    if (!id) return;
    this.reviews.getJob(id).subscribe({
      next: (job) => {
        this._job.set(job);
        if (job.state === 'done') {
          this.loadFindings(job);
          forgetJob();
        } else if (job.state === 'error') {
          forgetJob();
        } else {
          this.follow(id);
        }
      },
      // Gone, expired, or someone else's — nothing to resume.
      error: () => forgetJob(),
    });
  }

  /** Retry watching a job we lost contact with. */
  reconnect(): void {
    const id = this._job()?.id ?? rememberedJob();
    if (id) this.follow(id);
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
    forgetJob();
    this._lostContact.set(false);
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
