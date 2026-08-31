/**
 * Training mode: markup, feedback, and the owner's queue.
 *
 * Separate from ReviewService rather than bolted onto it. That service owns one
 * thing — submitting a review and following it to a result — and it is the only
 * thing every user touches. This is a second surface, reachable from a finished
 * review, and most deployments will not have it switched on at all.
 *
 * Signals throughout, as the rest of the app. There is no polling here, so RxJS
 * stays where it earns its place and does not spread to state that a signal
 * holds perfectly well.
 */
import { Injectable, computed, inject, signal } from '@angular/core';

import {
  AdminApi,
  AdminOverview,
  Decision,
  CalibrationView,
  Feedback,
  FeedbackAccepted,
  FeedbackRequest,
  Markup,
  MarkupExport,
  MarkupRequest,
  PromptExport,
  SweepRequest,
  TrainingApi,
} from '../api';
import { ApiFailure, toFailure } from '../review/review-service';

@Injectable({ providedIn: 'root' })
export class TrainingService {
  private readonly training = inject(TrainingApi);
  private readonly admin = inject(AdminApi);

  private readonly _markups = signal<Markup[]>([]);
  private readonly _feedback = signal<Feedback[]>([]);
  private readonly _saving = signal(false);
  private readonly _failure = signal<ApiFailure | null>(null);
  private readonly _lastAccepted = signal<FeedbackAccepted | null>(null);
  private readonly _export = signal<MarkupExport | null>(null);

  private readonly _queue = signal<Feedback[]>([]);
  private readonly _overview = signal<AdminOverview | null>(null);
  private readonly _calibration = signal<CalibrationView | null>(null);
  private readonly _prompt = signal<PromptExport | null>(null);
  private readonly _busy = signal(false);

  readonly markups = this._markups.asReadonly();
  readonly feedback = this._feedback.asReadonly();
  readonly saving = this._saving.asReadonly();
  readonly failure = this._failure.asReadonly();
  /** The triage verdict from the last submission, shown back to the submitter. */
  readonly lastAccepted = this._lastAccepted.asReadonly();
  /** The annotated pass, once it has been asked for. */
  readonly markupExport = this._export.asReadonly();

  readonly queue = this._queue.asReadonly();
  readonly overview = this._overview.asReadonly();
  readonly calibration = this._calibration.asReadonly();
  readonly prompt = this._prompt.asReadonly();
  readonly busy = this._busy.asReadonly();

  /** Findings this reviewer has already had their say about. */
  readonly reviewedFids = computed(
    () => new Set(this._feedback().map((f) => f.finding_fid).filter(Boolean)),
  );

  // ── markup ──────────────────────────────────────────────────────────────
  loadMarkups(jobId: string): void {
    this.training.listMarkups(jobId).subscribe({
      next: (page) => this._markups.set(page.markups),
      // A viewer that cannot list markup is still a usable viewer. Failing
      // loudly here would put an error banner over a working document.
      error: () => this._markups.set([]),
    });
  }

  createMarkup(jobId: string, body: MarkupRequest, done?: (markup: Markup) => void): void {
    this._saving.set(true);
    this.training.createMarkup(jobId, body).subscribe({
      next: (markup) => {
        this._markups.update((all) => [...all, markup]);
        this._saving.set(false);
        this._failure.set(null);
        // The caller gets the server's copy, not its own optimistic one: the
        // id is assigned here, and selecting a shape you cannot address is
        // the same as not selecting it.
        done?.(markup);
      },
      error: (error) => this.fail(error),
    });
  }

  // ── the annotated pass ──────────────────────────────────────────────────
  /** Fetch the whole pass so it can be read, kept, or handed over. */
  loadExport(jobId: string): void {
    this._busy.set(true);
    this.training.exportMarkups(jobId).subscribe({
      next: (bundle) => {
        this._export.set(bundle);
        this._busy.set(false);
        this._failure.set(null);
      },
      error: (error) => this.fail(error),
    });
  }

  clearExport(): void {
    this._export.set(null);
  }

  /**
   * Hand the pass over.
   *
   * Sends no markup: the server reads its own store, so what the owner is
   * handed is what was actually drawn rather than what a browser claims was.
   */
  submitPass(jobId: string, body: SweepRequest): void {
    this._saving.set(true);
    this._lastAccepted.set(null);
    this.training.submitMarkupPass(jobId, body).subscribe({
      next: (accepted) => {
        this._saving.set(false);
        this._failure.set(null);
        this._lastAccepted.set(accepted);
        this.loadFeedback(jobId);
      },
      error: (error) => this.fail(error),
    });
  }

  updateMarkup(jobId: string, markup: Markup, body: MarkupRequest): void {
    this._saving.set(true);
    this.training.updateMarkup(jobId, markup.id, body).subscribe({
      next: (saved) => {
        this._markups.update((all) => all.map((m) => (m.id === saved.id ? saved : m)));
        this._saving.set(false);
        this._failure.set(null);
      },
      error: (error) => this.fail(error),
    });
  }

  deleteMarkup(jobId: string, markupId: string): void {
    this._saving.set(true);
    this.training.deleteMarkup(jobId, markupId).subscribe({
      // The server answers with what is left rather than an empty 204, so the
      // overlay redraws from what it actually holds.
      next: (page) => {
        this._markups.set(page.markups);
        this._saving.set(false);
      },
      error: (error) => this.fail(error),
    });
  }

  // ── feedback ────────────────────────────────────────────────────────────
  loadFeedback(jobId: string): void {
    this.training.listJobFeedback(jobId).subscribe({
      next: (page) => this._feedback.set(page.feedback),
      error: () => this._feedback.set([]),
    });
  }

  submitFeedback(jobId: string, body: FeedbackRequest, done?: () => void): void {
    this._saving.set(true);
    this._lastAccepted.set(null);
    this.training.submitFeedback(jobId, body).subscribe({
      next: (accepted) => {
        this._saving.set(false);
        this._failure.set(null);
        this._lastAccepted.set(accepted);
        this.loadFeedback(jobId);
        done?.();
      },
      error: (error) => this.fail(error),
    });
  }

  clearAccepted(): void {
    this._lastAccepted.set(null);
  }

  // ── the owner's queue ───────────────────────────────────────────────────
  loadQueue(state = 'new'): void {
    this._busy.set(true);
    this.admin.adminListFeedback(state || undefined).subscribe({
      next: (page) => {
        this._queue.set(page.feedback);
        this._busy.set(false);
      },
      error: (error) => this.fail(error),
    });
    this.admin.adminOverview().subscribe({
      next: (overview) => this._overview.set(overview),
      error: () => this._overview.set(null),
    });
  }

  loadCalibration(): void {
    this.admin.adminCalibration().subscribe({
      next: (view) => this._calibration.set(view),
      error: (error) => this.fail(error),
    });
  }

  decide(feedbackId: string, decision: Decision, note = ''): void {
    this._busy.set(true);
    this.admin.adminDecideFeedback(feedbackId, { decision, note }).subscribe({
      next: (updated) => {
        // Dropped from the queue rather than restyled in place: the queue is
        // "what still needs a decision", and a decided row that stays visible
        // is a row that gets decided twice.
        this._queue.update((all) => all.filter((f) => f.id !== updated.id));
        this._busy.set(false);
        this._failure.set(null);
        this.loadCalibration();
      },
      error: (error) => this.fail(error),
    });
  }

  loadPrompt(feedbackId: string): void {
    this._prompt.set(null);
    this.admin.adminFeedbackPrompt(feedbackId).subscribe({
      next: (exported) => this._prompt.set(exported),
      error: (error) => this.fail(error),
    });
  }

  closePrompt(): void {
    this._prompt.set(null);
  }

  openIssue(feedbackId: string): void {
    this._busy.set(true);
    this.admin.adminFeedbackIssue(feedbackId).subscribe({
      next: (created) => {
        this._busy.set(false);
        this._queue.update((all) =>
          all.map((f) => (f.id === feedbackId ? { ...f, issue_url: created.url } : f)),
        );
        if (!created.url) {
          this._failure.set({ code: 'github', message: created.status });
        }
      },
      error: (error) => this.fail(error),
    });
  }

  sendDigest(): void {
    this._busy.set(true);
    this.admin.adminSendDigest().subscribe({
      next: (status) => {
        this._busy.set(false);
        this._failure.set({ code: 'digest', message: status.status });
      },
      error: (error) => this.fail(error),
    });
  }

  dismissFailure(): void {
    this._failure.set(null);
  }

  private fail(error: unknown): void {
    this._saving.set(false);
    this._busy.set(false);
    this._failure.set(toFailure(error));
  }
}
