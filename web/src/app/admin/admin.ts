/**
 * The owner's queue — a section of the training console, not a page of its own.
 *
 * It used to be `/admin`, reached from a second entry in the masthead, and that
 * split was wrong in the way that matters: the two halves are one loop. A
 * reviewer argues with a rule in the workspace, the argument lands here, and
 * what gets approved here is what the next review runs against. Putting a
 * navigation step between the proposal and the decision made the queue somewhere
 * you had to remember to go, and a queue nobody visits is a queue that grows.
 *
 * So it renders inside `Training`, below the reviews it is fed by, and only for
 * an owner. `/admin` still resolves — it redirects — because the URL was handed
 * out and a dead bookmark is not an improvement.
 *
 * Everything a reviewer submits lands here with the triage's verdict already
 * attached, so the work is deciding rather than reading. Three actions, matching
 * the three ways a report can be resolved:
 *
 * * **Approve** — only offered where a calibration proposal exists. It promotes
 *   the change into a new profile version, and that version is what every
 *   standard review runs against from then on. This is the one button in the
 *   application that changes what other people are told about their buildings,
 *   and it is deliberately never automatic.
 * * **Export a prompt** — the report written up as a runnable brief, in the
 *   same shape as the files already in `docs/`. This is how a `needs_component`
 *   report becomes work.
 * * **Open an issue** — the same brief, tracked, for anything that will outlive
 *   one session.
 */
import { DatePipe, KeyValuePipe } from '@angular/common';
import { Component, computed, inject, input, signal } from '@angular/core';

import { Decision, Disposition, Feedback } from '../api';
import { ReviewService } from '../review/review-service';
import { TrainingService } from '../training/training-service';

@Component({
  selector: 'app-admin',
  imports: [DatePipe, KeyValuePipe],
  templateUrl: './admin.html',
})
export class Admin {
  protected readonly training = inject(TrainingService);
  private readonly reviews = inject(ReviewService);

  /** Which step this is on the page. The queue is 02 for an owner and absent
   *  for anybody else, so the number is the parent's to decide. */
  readonly mark = input('02');

  /** Both filters are the service's, because the counts above the queue drive
   *  them as well as the buttons below it. */
  protected readonly state = this.training.queueState;
  protected readonly disposition = this.training.queueDisposition;
  protected readonly expanded = signal<string>('');
  protected readonly note = signal('');

  protected readonly overview = this.training.overview;
  protected readonly queue = this.training.queue;
  protected readonly calibration = this.training.calibration;
  protected readonly prompt = this.training.prompt;
  protected readonly busy = this.training.busy;

  protected readonly dispositions = computed(
    () => this.reviews.config()?.dispositions ?? [],
  );

  /** Rules the active profile changes, for the calibration panel. */
  protected readonly calibrated = computed(() => {
    const active = this.calibration()?.active;
    if (!active) return [];
    return Object.entries(active.rules ?? {})
      .map(([rule_id, rule]) => ({ rule_id, rule }))
      .sort((a, b) => a.rule_id.localeCompare(b.rule_id));
  });

  constructor() {
    this.reviews.loadConfig();
    this.training.loadQueue(this.state(), this.disposition());
    this.training.loadCalibration();
  }

  /** Show one state, clearing any disposition narrowing. */
  protected show(state: string): void {
    this.training.loadQueue(state, '');
  }

  /** Everything still waiting on a decision — what the counts call "waiting". */
  protected showWaiting(): void {
    this.training.loadQueue('new', '');
  }

  /**
   * Narrow the waiting items to one disposition.
   *
   * The counts are of open feedback by disposition, so the state goes back to
   * `new` alongside: a count of two "needs a new component" that lands you on a
   * list of none, because the filter was still on `actioned`, is worse than not
   * being able to click it at all.
   */
  protected showDisposition(key: string): void {
    this.training.loadQueue('new', key);
  }

  /**
   * How many open items carry one disposition.
   *
   * A method rather than `summary.counts[key]` in the template, because the
   * generated type is a plain index signature: TypeScript says the lookup is a
   * `number` and at runtime a disposition nobody has used is `undefined`, which
   * rendered as an empty tile where a zero belonged.
   */
  protected countFor(counts: { [key: string]: number }, key: string): number {
    return counts[key] ?? 0;
  }

  /** A disposition's own words, for the line saying what the queue is narrowed to. */
  protected dispositionLabel(key: string): string {
    return this.dispositions().find((d) => d.key === key)?.label ?? key;
  }

  protected scrollTo(id: string): void {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  protected toggle(item: Feedback): void {
    this.expanded.update((id) => (id === item.id ? '' : item.id));
    this.note.set('');
  }

  protected onNote(event: Event): void {
    this.note.set((event.target as HTMLTextAreaElement).value);
  }

  protected canApprove(item: Feedback): boolean {
    return (item.triage.changes?.length ?? 0) > 0 && item.disposition === Disposition.AutoTunable;
  }

  protected accept(item: Feedback): void {
    this.training.decide(item.id, Decision.Accept, this.note());
    this.note.set('');
  }

  protected reject(item: Feedback): void {
    this.training.decide(item.id, Decision.Reject, this.note());
    this.note.set('');
  }

  protected action(item: Feedback): void {
    this.training.decide(item.id, Decision.Action, this.note());
    this.note.set('');
  }

  protected async copyPrompt(): Promise<void> {
    const text = this.prompt()?.markdown;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Clipboard access is refused in plenty of ordinary situations. The
      // markdown is on screen and selectable either way, so this is a
      // convenience failing, not the feature failing.
    }
  }
}
