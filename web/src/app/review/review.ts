/**
 * The tool page: upload, parameters, progress, results.
 *
 * One page, as the reference implementation was. The form is driven by
 * GET /api/config rather than hard-coded, so adding an occupancy group to
 * fbcreview/options.py needs no change here.
 */
import { DecimalPipe } from '@angular/common';
import { Component, OnDestroy, computed, inject, signal, viewChild } from '@angular/core';
import { FormControl, FormGroup, ReactiveFormsModule } from '@angular/forms';

import { Finding, ReviewOptions, ReviewOptionsMinSeverityEnum } from '../api';
import { AuthService } from '../core/auth';
import { DeclarationForm } from './declaration/declaration-form';
import { ReviewService } from './review-service';

/** Tally order. VERIFIED and MEASURED last: they are coverage, not problems. */
const TALLY = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'MEASURED', 'VERIFIED'] as const;

/**
 * Review settings only. Occupancy group and sprinkler status used to live here
 * and were always in the wrong place — they are facts about the building, and
 * they are now questions 1 and 9 of the project declaration, where they are
 * reconciled against the drawings instead of overriding them.
 */
interface OptionsForm {
  project_name: FormControl<string>;
  edition: FormControl<string>;
  min_severity: FormControl<ReviewOptionsMinSeverityEnum>;
  include_verified: FormControl<boolean>;
  include_measured: FormControl<boolean>;
  convert_raster: FormControl<boolean>;
}

@Component({
  selector: 'app-review',
  imports: [ReactiveFormsModule, DecimalPipe, DeclarationForm],
  templateUrl: './review.html',
})
export class Review implements OnDestroy {
  private readonly reviews = inject(ReviewService);
  protected readonly auth = inject(AuthService);

  protected readonly config = this.reviews.config;
  protected readonly job = this.reviews.job;
  protected readonly findings = this.reviews.findings;
  protected readonly failure = this.reviews.failure;
  protected readonly submitting = this.reviews.submitting;
  protected readonly running = this.reviews.running;
  protected readonly lostContact = this.reviews.lostContact;

  protected readonly declarationForm = viewChild(DeclarationForm);

  protected readonly file = signal<File | null>(null);
  protected readonly dragging = signal(false);
  protected readonly clientError = signal<string | null>(null);
  protected readonly tallyOrder = TALLY;

  protected readonly form = new FormGroup<OptionsForm>({
    project_name: new FormControl('', { nonNullable: true }),
    edition: new FormControl('fbc2023', { nonNullable: true }),
    min_severity: new FormControl(ReviewOptionsMinSeverityEnum.Low, { nonNullable: true }),
    include_verified: new FormControl(true, { nonNullable: true }),
    include_measured: new FormControl(true, { nonNullable: true }),
    convert_raster: new FormControl(false, { nonNullable: true }),
  });

  /** Shown while the job is queued or running, and after it finishes. */
  protected readonly stageIndex = computed(() => this.job()?.stage ?? 0);
  protected readonly stages = computed(() => this.job()?.stages ?? []);

  private readonly counts = computed<Record<string, number>>(
    () => this.job()?.summary?.counts ?? {},
  );

  /** Severities absent from the map are genuinely zero, not missing. */
  protected count(severity: string): number {
    return this.counts()[severity] ?? 0;
  }

  protected readonly critical = computed<Finding | null>(
    () => this.findings()?.find((f) => f.severity === 'CRITICAL') ?? null,
  );

  protected readonly abstentions = computed(() => this.job()?.summary?.abstentions ?? []);

  /** Fields where the declaration and the drawings disagree. */
  protected readonly conflicts = computed(
    () => this.findings()?.filter((f) => f.status === 'CONFLICT') ?? [],
  );

  /** Findings that rest on an answer rather than on anything printed. */
  protected readonly declaredBasis = computed(
    () => this.findings()?.filter((f) => f.basis === 'declaration') ?? [],
  );

  /** Checks that came out differently under the two readings. */
  protected readonly divergent = computed(
    () => this.findings()?.filter((f) => f.scenario !== 'both') ?? [],
  );

  protected scenarioNote(finding: Finding): string {
    const failing = finding.status === 'OPEN' || finding.status === 'CONFLICT';
    if (finding.scenario === 'as_drawn') {
      return failing
        ? 'Fails as drawn. Passes as you described it.'
        : 'Holds as drawn. Comes out differently as you described it.';
    }
    if (finding.scenario === 'as_declared') {
      return failing
        ? 'Passes as drawn. Fails as you described it.'
        : 'Holds as you described it. Comes out differently as drawn.';
    }
    return '';
  }

  /** Sheets the parser could not read into. Not the same as sheets that passed. */
  protected readonly unreadableSheets = computed(() => this.job()?.source?.raster_pages ?? []);

  /**
   * Readable sheets that paste part of the drawing in as a picture. This is the
   * common case, and the quiet one: the sheet is genuinely vector, so nothing
   * looks wrong, while the code-analysis table on it is pixels.
   */
  protected readonly pastedTableSheets = computed(() => this.job()?.source?.region_pages ?? []);

  /** True when those sheets went unread because the rebuild was left off. */
  protected readonly pastedTablesUnread = computed(
    () => this.pastedTableSheets().length > 0 && !this.job()?.conversion,
  );

  constructor() {
    // Adopt the server's defaults once, rather than duplicating them here
    // where they would drift.
    //
    // Deliberately a one-shot callback and not an effect() on config(): an
    // effect re-runs whenever the signal changes, so any later refetch — a
    // retry, a reconnect — would silently reset the occupancy group and the
    // sprinklered checkbox underneath someone who had already changed them.
    this.reviews.loadConfig((config) => this.applyDefaults(config.defaults));
    // A reload must not strand a review that is still running, or one that has
    // already finished, on the server.
    this.reviews.resumeLastJob();
  }

  private applyDefaults(defaults: ReviewOptions): void {
    this.form.patchValue(
      {
        edition: defaults.edition ?? 'fbc2023',
        min_severity: defaults.min_severity ?? ReviewOptionsMinSeverityEnum.Low,
        include_verified: defaults.include_verified ?? true,
        include_measured: defaults.include_measured ?? true,
      },
      { emitEvent: false },
    );
  }

  ngOnDestroy(): void {
    // Navigating away must stop the poll.
    this.reviews.stop();
  }

  // ── file selection ──────────────────────────────────────────────────────
  protected onFileChosen(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.accept(input.files?.[0] ?? null);
  }

  protected onDragOver(event: DragEvent): void {
    event.preventDefault();
    this.dragging.set(true);
  }

  protected onDragLeave(): void {
    this.dragging.set(false);
  }

  protected onDrop(event: DragEvent): void {
    event.preventDefault();
    this.dragging.set(false);
    this.accept(event.dataTransfer?.files?.[0] ?? null);
  }

  /**
   * Client-side checks are for immediate feedback, not for safety — the server
   * checks all of this again and is the only place it counts.
   */
  private accept(file: File | null): void {
    if (!file) return;
    this.clientError.set(null);

    if (!/\.pdf$/i.test(file.name)) {
      this.clientError.set('That is not a PDF. Upload the permit set as a PDF plotted from CAD.');
      return;
    }
    const limit = this.config()?.max_upload_mb ?? 120;
    if (file.size > limit * 1024 * 1024) {
      this.clientError.set(
        `That file is ${(file.size / 1048576).toFixed(1)} MB, over the ${limit} MB limit.`,
      );
      return;
    }

    this.file.set(file);
    if (!this.form.controls.project_name.value) {
      this.form.controls.project_name.setValue(file.name.replace(/\.pdf$/i, ''));
    }
  }

  protected fileLabel(): string {
    const file = this.file();
    return file ? `${file.name} · ${(file.size / 1048576).toFixed(1)} MB` : '';
  }

  // ── submit ──────────────────────────────────────────────────────────────
  protected submit(): void {
    const file = this.file();
    if (!file || this.submitting()) return;

    const value = this.form.getRawValue();
    // `occupancy_group` and `sprinklered` are deliberately not sent: the server
    // treats them as a deprecated bridge into the declaration, and sending the
    // form's untouched default would turn "nobody said" into "the user said".
    const options: ReviewOptions = {
      edition: value.edition,
      min_severity: value.min_severity,
      include_verified: value.include_verified,
      include_measured: value.include_measured,
      convert_raster: value.convert_raster,
      project_name: value.project_name,
    };
    this.reviews.submit(file, options, this.declarationForm()?.value() ?? {});
  }

  protected startOver(): void {
    this.reviews.reset();
    this.file.set(null);
    this.clientError.set(null);
  }

  protected reconnect(): void {
    this.reviews.reconnect();
  }

  /** Retries the current file with conversion switched on. */
  protected retryWithConversion(): void {
    this.form.controls.convert_raster.setValue(true);
    this.submit();
  }

  protected readonly showConversionOffer = computed(
    () => this.failure()?.code === 'raster_pdf' && this.file() !== null,
  );

  // ── downloads ───────────────────────────────────────────────────────────
  /**
   * Resolves a signed URL that has not expired, then navigates to it. Done in
   * code rather than a plain href because an hour-old page would otherwise
   * offer a link that 403s.
   */
  protected async download(which: 'markup_pdf' | 'findings_json'): Promise<void> {
    const url = await this.reviews.freshDownload(which);
    if (url) window.location.href = url;
  }

  protected megabytes(bytes: number | undefined): number {
    return (bytes ?? 0) / 1048576;
  }
}
