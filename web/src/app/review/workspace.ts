/**
 * The review workspace: the set, its findings, and what you make of them.
 *
 * A separate route from the upload page rather than a panel on it. The upload
 * page is a form that ends in a result; this is somewhere you sit and work
 * through a set sheet by sheet, and it wants the whole window. It is also
 * reachable by URL, which matters — `/review/{id}` is what you send someone
 * when you want them to look at sheet 12 with you.
 *
 * Lazily routed, which is also what keeps pdf.js out of the initial bundle.
 */
import { DatePipe, LowerCasePipe } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { map } from 'rxjs';

import { Feedback, FeedbackSubject, Finding, Markup, MarkupRequest } from '../api';
import { FeedbackDraft, FeedbackPanel } from '../feedback/feedback-panel';
import { SheetViewer } from '../viewer/sheet-viewer';
import { TrainingService } from '../training/training-service';
import { ReviewService } from './review-service';

@Component({
  selector: 'app-workspace',
  imports: [SheetViewer, FeedbackPanel, RouterLink, DatePipe, LowerCasePipe],
  templateUrl: './workspace.html',
})
export class Workspace {
  private readonly route = inject(ActivatedRoute);
  private readonly reviews = inject(ReviewService);
  protected readonly training = inject(TrainingService);

  protected readonly job = this.reviews.job;
  protected readonly findings = this.reviews.findings;
  protected readonly config = this.reviews.config;
  protected readonly failure = this.reviews.failure;

  protected readonly selectedFinding = signal<Finding | null>(null);
  protected readonly selectedMarkup = signal<Markup | null>(null);
  /** Which kind of feedback the panel is collecting right now. */
  protected readonly subject = signal<FeedbackSubject>(FeedbackSubject.Finding);
  protected readonly commentDraft = signal('');

  /**
   * The review being looked at.
   *
   * Read from the `paramMap` stream rather than `route.snapshot`. The router
   * reuses this component when only the id changes — following a link from one
   * review to another — and a snapshot is captured once, so the second review
   * would never load.
   */
  protected readonly jobId = toSignal(
    this.route.paramMap.pipe(map((params) => params.get('id') ?? '')),
    { initialValue: '' },
  );

  protected readonly trainingOn = computed(
    () => this.config()?.training?.enabled ?? false,
  );
  protected readonly source = computed(() => this.job()?.downloads?.source_pdf ?? '');
  protected readonly aspects = computed(() => this.config()?.feedback_aspects ?? []);
  protected readonly markupKinds = computed(() => this.config()?.markup_kinds ?? []);

  protected readonly open = computed(() =>
    (this.findings() ?? []).filter((f) => f.status !== 'PASS'),
  );

  /** Feedback this reviewer has already given, keyed by what it was about. */
  protected readonly saidAbout = computed(() => {
    const map = new Map<string, Feedback>();
    for (const item of this.training.feedback()) {
      if (item.finding_fid) map.set(item.finding_fid, item);
    }
    return map;
  });

  constructor() {
    this.reviews.loadConfig();

    effect(() => {
      const id = this.jobId();
      if (!id) return;
      // Opening a different review must not leave the previous one's selection
      // and markup on screen.
      this.selectedFinding.set(null);
      this.selectedMarkup.set(null);
      this.training.clearAccepted();
      this.reviews.open(id);
    });

    // Markup and feedback only exist on a deployment that collects them, and
    // only once the config has said so — asking first would 400 on every
    // deployment that has training switched off.
    effect(() => {
      const id = this.jobId();
      if (id && this.trainingOn()) {
        this.training.loadMarkups(id);
        this.training.loadFeedback(id);
      }
    });
  }

  // ── selection ───────────────────────────────────────────────────────────
  protected pickFinding(finding: Finding): void {
    this.selectedFinding.set(finding);
    this.selectedMarkup.set(null);
    this.subject.set(FeedbackSubject.Finding);
    this.training.clearAccepted();
  }

  protected pickMarkup(markup: Markup): void {
    this.selectedMarkup.set(markup);
    this.commentDraft.set(markup.comment ?? '');
    this.training.clearAccepted();
  }

  protected clearSelection(): void {
    this.selectedFinding.set(null);
    this.selectedMarkup.set(null);
    this.training.clearAccepted();
  }

  /** Switch the form to "this sheet is missing a check", anchored to a markup. */
  protected reportGap(markup: Markup): void {
    this.selectedMarkup.set(markup);
    this.selectedFinding.set(null);
    this.subject.set(FeedbackSubject.Coverage);
    this.training.clearAccepted();
  }

  // ── markup ──────────────────────────────────────────────────────────────
  protected onDrawn(request: MarkupRequest): void {
    this.training.createMarkup(this.jobId(), request);
  }

  protected onCommentInput(event: Event): void {
    this.commentDraft.set((event.target as HTMLTextAreaElement).value);
  }

  protected saveComment(): void {
    const markup = this.selectedMarkup();
    if (!markup) return;
    this.training.updateMarkup(this.jobId(), markup, {
      page: markup.page,
      kind: markup.kind,
      geometry: markup.geometry,
      comment: this.commentDraft(),
      colour: markup.colour ?? '',
      sheet: markup.sheet ?? '',
      finding_fid: markup.finding_fid ?? '',
    });
  }

  protected removeMarkup(markup: Markup): void {
    this.training.deleteMarkup(this.jobId(), markup.id);
    if (this.selectedMarkup()?.id === markup.id) this.selectedMarkup.set(null);
  }

  // ── feedback ────────────────────────────────────────────────────────────
  protected onFeedback(draft: FeedbackDraft): void {
    this.training.submitFeedback(this.jobId(), {
      subject: this.subject(),
      finding_fid: this.selectedFinding()?.fid ?? '',
      markup_id: this.selectedMarkup()?.id ?? '',
      answers: draft.answers,
      comment: draft.comment,
    });
  }

  protected feedbackHeadline(): string {
    if (this.subject() === FeedbackSubject.Coverage) {
      return 'What should this review have caught here?';
    }
    const finding = this.selectedFinding();
    return finding ? `${finding.fid} · ${finding.title}` : '';
  }
}
