/**
 * The Project Declaration questionnaire.
 *
 * Every question, every label, every enum value and every help text arrives from
 * `GET /api/config`. Nothing here is hard-coded, and nothing may be: a code
 * label that exists in two places will drift, and a wrong code label on a form
 * a designer answers is a liability rather than a typo.
 *
 * The form is a `FormRecord<FormControl<string>>` — one control per served
 * field, all holding strings — because the field list is data and cannot be
 * written out as a static interface. Everything is converted to its declared
 * type once, on the way out, in `value()`.
 */
import { Component, computed, effect, input, signal } from '@angular/core';
import { FormControl, FormRecord, ReactiveFormsModule } from '@angular/forms';

import { DeclarationField, DeclarationGroup, PrefilledField, ProjectDeclaration } from '../../api';

export type Vocabulary = 'pro' | 'simple';

/** Survives a reload. A designer who wants the code words wants them every time. */
const MODE_KEY = 'fbc.declarationVocabulary';

function rememberedMode(): Vocabulary {
  try {
    return localStorage.getItem(MODE_KEY) === 'simple' ? 'simple' : 'pro';
  } catch {
    // Private mode, storage disabled. The default is the answer.
    return 'pro';
  }
}

function rememberMode(mode: Vocabulary): void {
  try {
    localStorage.setItem(MODE_KEY, mode);
  } catch {
    /* as above — remembering the choice is a convenience, not a need */
  }
}

@Component({
  selector: 'app-declaration-form',
  imports: [ReactiveFormsModule],
  templateUrl: './declaration-form.html',
})
export class DeclarationForm {
  readonly fields = input.required<DeclarationField[]>();
  readonly groups = input.required<DeclarationGroup[]>();
  /** Every rule any field can unlock. Served, so the count is never invented. */
  readonly unlockable = input<string[]>([]);

  /**
   * What the drawings state, from `POST /api/prefill`. Offered, never imposed:
   * a suggested answer is filled in but stays marked until the applicant looks
   * at it, because the declaration is an assertion by a person and the
   * reconciliation is only worth running if the two sources are independent.
   * A silently auto-accepted value would make every field agree with itself.
   */
  readonly suggestions = input<PrefilledField[]>([]);

  protected readonly mode = signal<Vocabulary>(rememberedMode());

  /** Field keys that hold a suggestion the applicant has not yet confirmed. */
  private readonly unconfirmed = signal<ReadonlySet<string>>(new Set());

  /** Where each suggestion was read, for the note under the field. */
  private readonly sources = signal<ReadonlyMap<string, PrefilledField>>(new Map());

  constructor() {
    // Applying a suggestion writes into controls, so it belongs in an effect
    // rather than a computed. It runs when a new set is read, and only fills
    // blanks: an answer already typed is the applicant's and is never
    // overwritten by the drawings.
    effect(() => {
      const found = this.suggestions();
      const record = this.form();
      if (!found.length) return;

      const pending = new Set<string>();
      const where = new Map<string, PrefilledField>();
      for (const suggestion of found) {
        const control = record.controls[suggestion.key];
        if (!control) continue;
        where.set(suggestion.key, suggestion);
        if ((control.value ?? '').trim()) continue;
        control.setValue(suggestion.value);
        pending.add(suggestion.key);
      }
      this.sources.set(where);
      this.unconfirmed.set(pending);
      this.onEdit();
    });
  }

  /**
   * One string control per served field, built the first time the schema
   * arrives. `computed` rather than an effect: the controls are derived from
   * the field list and nothing else.
   */
  protected readonly form = computed(() => {
    const record = new FormRecord<FormControl<string>>({});
    for (const field of this.fields()) {
      record.addControl(field.key, new FormControl('', { nonNullable: true }));
    }
    return record;
  });

  /**
   * Bumped by every edit. The answered count has to be live, and a counter the
   * template increments is the whole of what RxJS would buy here — this app
   * keeps observables for the polling loop, where they earn their place.
   */
  private readonly edits = signal(0);

  protected readonly answers = computed<Record<string, string>>(() => {
    this.edits();
    return this.form().getRawValue();
  });

  protected onEdit(): void {
    this.edits.update((n) => n + 1);
  }

  /** Typing over a suggestion is the strongest possible confirmation. */
  protected onFieldEdit(key: string): void {
    this.confirm(key);
    this.onEdit();
  }

  protected suggestionFor(key: string): PrefilledField | undefined {
    return this.sources().get(key);
  }

  protected isUnconfirmed(key: string): boolean {
    return this.unconfirmed().has(key);
  }

  protected confirm(key: string): void {
    if (!this.unconfirmed().has(key)) return;
    const next = new Set(this.unconfirmed());
    next.delete(key);
    this.unconfirmed.set(next);
  }

  protected confirmAll(): void {
    this.unconfirmed.set(new Set());
  }

  /** How many read-off answers the applicant has not looked at yet. */
  readonly pendingCount = computed(() => this.unconfirmed().size);

  /** How many answers came off the drawings at all, confirmed or not. */
  protected readonly suggestedCount = computed(
    () => this.suggestions().filter((s) => this.form().controls[s.key]).length,
  );

  protected readonly answered = computed(
    () => this.fields().filter((f) => this.isAnswered(f.key)).length,
  );

  protected readonly total = computed(() => this.fields().length);

  /**
   * A rule runs only when every field that feeds it has been answered, so a
   * rule is still standing down while any one of its inputs is blank. That is
   * the honest number to show: it is what the engine will refuse to guess at.
   */
  protected readonly standingDown = computed(() => {
    const answered = new Set(
      this.fields()
        .filter((f) => this.isAnswered(f.key))
        .map((f) => f.key),
    );
    return this.unlockable().filter((rule) =>
      this.fields().some((f) => (f.unlocks ?? []).includes(rule) && !answered.has(f.key)),
    ).length;
  });

  protected readonly anyAnswered = computed(() => this.answered() > 0);

  protected fieldsIn(group: string): DeclarationField[] {
    return this.fields().filter((f) => f.group === group);
  }

  protected answeredIn(group: string): number {
    return this.fieldsIn(group).filter((f) => this.isAnswered(f.key)).length;
  }

  protected label(field: DeclarationField): string {
    return this.mode() === 'simple' ? field.simple_label : field.pro_label;
  }

  protected help(field: DeclarationField): string {
    return this.mode() === 'simple' ? field.simple_help : field.pro_help;
  }

  protected choiceLabel(field: DeclarationField, choice: string): string {
    return field.choice_labels?.[choice] ?? choice;
  }

  protected setMode(mode: Vocabulary): void {
    this.mode.set(mode);
    rememberMode(mode);
  }

  protected clear(): void {
    this.form().reset();
    this.unconfirmed.set(new Set());
    this.onEdit();
  }

  private isAnswered(key: string): boolean {
    return (this.answers()[key] ?? '').trim() !== '';
  }

  /**
   * The declaration as the API takes it.
   *
   * A blank control is omitted rather than sent as `null` or as an empty
   * string: unanswered has exactly one representation, and the engine abstains
   * on it. Nothing here supplies a default for a question nobody answered.
   */
  value(): ProjectDeclaration {
    const raw = this.form().getRawValue();
    const out: Record<string, string | number | boolean> = {};
    for (const field of this.fields()) {
      const text = (raw[field.key] ?? '').trim();
      if (!text) continue;
      if (field.kind === 'bool') {
        out[field.key] = text === 'true';
      } else if (field.kind === 'number' || field.kind === 'integer') {
        const value = Number(text.replace(/,/g, ''));
        if (Number.isFinite(value)) out[field.key] = value;
      } else {
        out[field.key] = text;
      }
    }
    return out as ProjectDeclaration;
  }
}
