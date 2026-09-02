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
import { Component, computed, inject, signal } from '@angular/core';

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

  protected readonly state = signal('new');
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
    this.training.loadQueue(this.state());
    this.training.loadCalibration();
  }

  protected show(state: string): void {
    this.state.set(state);
    this.training.loadQueue(state);
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
