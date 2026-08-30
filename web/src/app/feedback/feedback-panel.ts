/**
 * The structured feedback form.
 *
 * Every question, every answer and every piece of help text comes off the wire
 * from `GET /api/config`. This component carries no copy of the taxonomy, for
 * the same reason `DeclarationForm` carries no copy of a code label: a label
 * that exists in two places drifts, and here the drift would be between what a
 * person was asked and what the triage thinks they answered.
 *
 * The shape of the form is the argument. "What was wrong with this?" over a
 * textarea makes each person invent their own breakdown, and leaves the triage
 * guessing at which of half a dozen unrelated failures they meant — a wrong
 * citation, a misread table, an overstated severity and an inapplicable rule
 * have nothing in common and are fixed in four different places. Asking six
 * separable questions, each with its own way of saying "this was right",
 * produces an answer the server can route without reading prose.
 *
 * The comment is kept, and it is not a formality: it is where the sentence that
 * no dropdown captures goes. But a submission carrying one always reaches a
 * person, so the form says so rather than implying the box is optional detail.
 */
import { Component, computed, input, output, signal } from '@angular/core';

import { FeedbackAccepted, FeedbackAspect, FeedbackSubject } from '../api';

export interface FeedbackDraft {
  answers: Record<string, string>;
  comment: string;
}

@Component({
  selector: 'app-feedback-panel',
  templateUrl: './feedback-panel.html',
})
export class FeedbackPanel {
  /** The whole published taxonomy; this filters it to the subject in hand. */
  readonly aspects = input<FeedbackAspect[]>([]);
  readonly subject = input<FeedbackSubject>(FeedbackSubject.Finding);
  readonly busy = input(false);
  /** What came back from the last submission, shown in place of the form. */
  readonly accepted = input<FeedbackAccepted | null>(null);
  readonly headline = input('');

  readonly submitted = output<FeedbackDraft>();
  readonly dismissed = output<void>();

  private readonly answers = signal<Record<string, string>>({});
  protected readonly comment = signal('');

  protected readonly asked = computed(() =>
    this.aspects().filter((a) => a.subject === this.subject()),
  );

  protected readonly answered = computed(() => Object.keys(this.answers()).length);

  protected readonly missingRequired = computed(() =>
    this.asked().filter((a) => a.required && !this.answers()[a.key]),
  );

  /** Nothing to send is not a submission. */
  protected readonly ready = computed(
    () =>
      !this.missingRequired().length &&
      (this.answered() > 0 || this.comment().trim().length > 0),
  );

  protected chosen(aspect: string): string {
    return this.answers()[aspect] ?? '';
  }

  protected choose(aspect: string, verdict: string): void {
    this.answers.update((all) => {
      const next = { ...all };
      // Clicking the chosen answer again clears it. An aspect nobody has an
      // opinion about must stay unanswered — a form that cannot be un-answered
      // collects a first guess and records it as a judgement.
      if (next[aspect] === verdict) delete next[aspect];
      else next[aspect] = verdict;
      return next;
    });
  }

  protected onComment(event: Event): void {
    this.comment.set((event.target as HTMLTextAreaElement).value);
  }

  protected send(): void {
    if (!this.ready() || this.busy()) return;
    this.submitted.emit({ answers: this.answers(), comment: this.comment().trim() });
  }

  /** Called by the host once a submission has been acknowledged. */
  reset(): void {
    this.answers.set({});
    this.comment.set('');
  }
}
