/**
 * The training console.
 *
 * ## Why this exists at all
 *
 * Training mode used to be a checkbox on the upload form. That made it a
 * property of a review you were about to run, which meant the only way to reach
 * any of it was to have a permit set in hand and be willing to wait forty
 * seconds. Everything a person actually wants from training mode — what have I
 * told it, what did it do with that, what is still waiting on somebody — was
 * unreachable without uploading a document that had nothing to do with the
 * question.
 *
 * So this is a destination rather than a mode. There is genuinely nothing to
 * train on without a document, but there is no reason it has to be a *new*
 * one: every finished review is already in storage with its findings and its
 * marked-up set, and opening one costs a fetch rather than a re-run.
 *
 * ## What it will not do
 *
 * It does not promote anything. A candidate profile is what your own feedback
 * has argued for and it changes nobody else's review; moving any of it into
 * production is the owner's decision and lives behind `/admin`. Showing the
 * candidate here and the approval there is the whole shape of the thing:
 * everyone can propose, one person decides, and the difference is visible.
 */
import { DatePipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { HistoryEntry } from '../api';
import { ReviewService } from '../review/review-service';
import { TrainingService } from './training-service';

/** Tally order. VERIFIED and MEASURED last: they are coverage, not problems. */
const TALLY = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'MEASURED', 'VERIFIED'] as const;

@Component({
  selector: 'app-training',
  imports: [RouterLink, DatePipe],
  templateUrl: './training.html',
})
export class Training {
  private readonly reviews = inject(ReviewService);
  protected readonly training = inject(TrainingService);

  protected readonly config = this.reviews.config;
  protected readonly history = this.reviews.history;
  protected readonly tallyOrder = TALLY;
  protected readonly showAll = signal(false);

  protected readonly status = computed(() => this.config()?.training ?? null);
  protected readonly enabled = computed(() => this.status()?.enabled ?? false);
  protected readonly isOwner = computed(() => this.status()?.is_owner ?? false);

  /** Reviews you can open and work through. Finished ones only. */
  protected readonly openable = computed(() =>
    (this.history() ?? []).filter((entry) => entry.state === 'done'),
  );

  protected readonly recent = computed(() =>
    this.showAll() ? this.openable() : this.openable().slice(0, 8),
  );

  /** What the feedback you have given was routed as. */
  protected readonly dispositions = computed(() => {
    const labels = new Map<string, string>();
    for (const info of this.config()?.dispositions ?? []) labels.set(info.key, info.label);
    return labels;
  });

  constructor() {
    this.reviews.loadConfig();
    this.reviews.loadHistory();
  }

  protected countOf(entry: HistoryEntry, severity: string): number {
    return entry.counts?.[severity] ?? 0;
  }

  protected worstOf(entry: HistoryEntry): string {
    for (const severity of TALLY) {
      if (this.countOf(entry, severity) > 0) return severity;
    }
    return 'VERIFIED';
  }

  protected toggleAll(): void {
    this.showAll.update((open) => !open);
  }
}
