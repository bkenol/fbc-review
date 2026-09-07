/**
 * Refine analysis: the whole loop on one page.
 *
 * ## The name
 *
 * This was "Training", and the word was doing damage. It promises a model
 * learning a check from examples, which is exactly what this cannot do and is
 * never going to do — the review path makes zero model calls. What actually
 * happens is that a reviewer's argument moves a named, versioned lever on a
 * rule somebody wrote by hand, and the analysis the next review produces is
 * sharper for it. "Refine analysis" says that; "training" says something else
 * and then has to be walked back in the first paragraph of the page.
 *
 * The mechanism keeps its old name where the old name is accurate:
 * `FBC_TRAINING_MODE`, `options.mode === 'training'` and `/api/admin/*` are
 * unchanged, because renaming a wire field to improve a heading is how a
 * client and a server stop agreeing.
 *
 * ## Why this exists at all
 *
 * It used to be a checkbox on the upload form. That made it a property of a
 * review you were about to run, which meant the only way to reach any of it was
 * to have a permit set in hand and be willing to wait forty seconds. Everything
 * a person actually wants here — what have I told it, what did it do with that,
 * what is still waiting on somebody — was unreachable without uploading a
 * document that had nothing to do with the question.
 *
 * So this is a destination rather than a mode. There is genuinely nothing to
 * refine without a document, but there is no reason it has to be a *new*
 * one: every finished review is already in storage with its findings and its
 * marked-up set, and opening one costs a fetch rather than a re-run.
 *
 * ## Why the queue is on this page
 *
 * It used to be `/admin`, a second route behind a second entry in the masthead.
 * That split described the permission — everyone may propose, one person
 * decides — but it described it by putting a navigation step in the middle of a
 * single loop. A reviewer argues with a rule in the workspace, the argument
 * lands in the queue, and what gets approved there is what the next review runs
 * against. Somewhere you have to remember to go is somewhere you stop going,
 * and a queue nobody opens is a queue that grows.
 *
 * So the queue renders here, below the reviews that feed it, and only for an
 * owner. The permission has not moved an inch. It never lived in the route
 * guard anyway: what enforces it is every `/api/admin/*` path answering 404 to
 * anybody who is not an owner, and that is untouched. The guard that used to
 * sit in front of `/admin` went with the route, because a guard whose only job
 * was to redirect away from a page everyone may open was doing nothing.
 */
import { DatePipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Admin } from '../admin/admin';
import { HistoryEntry } from '../api';
import { ReviewService } from '../review/review-service';
import { TrainingService } from './training-service';

/** Tally order. VERIFIED and MEASURED last: they are coverage, not problems. */
const TALLY = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'MEASURED', 'VERIFIED'] as const;

@Component({
  selector: 'app-training',
  imports: [Admin, RouterLink, DatePipe],
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

  /**
   * How much is sitting in the queue further down this page.
   *
   * Read from the overview the queue itself loads, so the two can never
   * disagree — and zero until it has answered, rather than a guess.
   */
  protected readonly waiting = computed(
    () => this.training.overview()?.open_feedback ?? 0,
  );

  /**
   * The step numbers, which depend on who is reading.
   *
   * The queue is section 02 for an owner and absent for everybody else, so the
   * two sections after it cannot carry a number written into the template — a
   * reviewer would read 01, 03, 04 and reasonably wonder what they were not
   * being shown.
   */
  protected readonly marks = computed(() => {
    const owner = this.isOwner();
    return {
      queue: '02',
      sets: owner ? '03' : '02',
      levers: owner ? '04' : '03',
    };
  });

  /**
   * Scroll to the queue rather than navigate to it.
   *
   * The button was `href="#queue"`, which reads as a fragment link and is one
   * everywhere except inside a router: Angular's default location strategy
   * resolved it as a route and landed on the home page instead. Selecting the
   * waiting state on the way means the button arrives at the items that are
   * actually asking for a decision, whatever was last filtered.
   */
  protected goToQueue(): void {
    this.training.loadQueue('new');
    document
      .getElementById('queue')
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

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
