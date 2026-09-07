/**
 * The one or two questions that would let a rule that stood down run.
 *
 * ## Why this is not the declaration form
 *
 * `DeclarationForm` asks the whole questionnaire, grouped, with both
 * vocabularies and a read-off-the-set confirmation flow. That is the right
 * shape at the top of an upload, where somebody is describing their building
 * before anything has happened.
 *
 * This is the wrong moment for any of that. The person is inside a finished
 * review, looking at one line of the abstention register:
 *
 *     DECL.BUILDING_AREA — neither the drawings nor the declaration state this
 *
 * They want to type a number and see the check run. Putting fifteen questions
 * in front of them to get to the one the rule is waiting on is how a remedy
 * ends up costing more than the finding it fixes, which is exactly why nobody
 * ever went back and answered these.
 *
 * So: the fields the rule actually named, nothing else, and a verb that says
 * what pressing it does.
 *
 * ## What it does not do
 *
 * It does not decide anything. The values go back to the parent as a partial
 * declaration; starting a review with them is the parent's call, and the server
 * validates them against the same schema `POST /api/review` uses. An answer
 * here is an assertion by a person in exactly the sense the full form means it
 * — which is why the copy says "state" rather than "set".
 */
import { Component, computed, input, output, signal } from '@angular/core';

import { DeclarationField, ProjectDeclaration } from '../../api';

@Component({
  selector: 'app-answer-now',
  templateUrl: './answer-now.html',
})
export class AnswerNow {
  /** The questions this rule named. Served metadata, never written out here. */
  readonly fields = input.required<DeclarationField[]>();
  /** What the review already has, so an answered field is not asked again. */
  readonly declared = input<ProjectDeclaration | null>(null);
  readonly busy = input(false);

  readonly answered = output<ProjectDeclaration>();

  /** Key → what has been typed, as a string. Converted once, on the way out. */
  private readonly typed = signal<Record<string, string>>({});

  /**
   * The questions still worth asking.
   *
   * A field the declaration already carries is not offered: the rule did not
   * stand down for want of *that*, and asking for it again would invite
   * somebody to overwrite an answer they gave deliberately.
   */
  protected readonly asking = computed(() => {
    const already = (this.declared() ?? {}) as Record<string, unknown>;
    return this.fields().filter((f) => {
      const value = already[f.key];
      return value === undefined || value === null || value === '';
    });
  });

  protected readonly anyTyped = computed(() =>
    Object.values(this.typed()).some((v) => v.trim() !== ''),
  );

  protected valueOf(key: string): string {
    return this.typed()[key] ?? '';
  }

  protected onInput(key: string, event: Event): void {
    const value = (event.target as HTMLInputElement | HTMLSelectElement).value;
    this.typed.update((all) => ({ ...all, [key]: value }));
  }

  protected labelFor(field: DeclarationField): string {
    return field.pro_label;
  }

  protected choiceLabel(field: DeclarationField, choice: string): string {
    return field.choice_labels?.[choice] ?? choice;
  }

  /**
   * Convert once, here, and send only what was typed.
   *
   * A blank stays out of the object entirely rather than going up as null: the
   * server merges this over what the review already declared, and null would be
   * indistinguishable from an instruction to withdraw an answer.
   */
  protected submit(): void {
    const out: Record<string, unknown> = {};
    for (const field of this.asking()) {
      const raw = (this.typed()[field.key] ?? '').trim();
      if (!raw) continue;
      if (field.kind === 'bool') out[field.key] = raw === 'true';
      else if (field.kind === 'integer') out[field.key] = parseInt(raw, 10);
      else if (field.kind === 'number') out[field.key] = Number(raw);
      else out[field.key] = raw;
    }
    if (Object.keys(out).length) this.answered.emit(out as ProjectDeclaration);
  }
}
