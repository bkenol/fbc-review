/**
 * The one or two questions a rule that stood down was waiting on.
 *
 * Two things here would bite if they were wrong, and neither is visible from
 * looking at the component: that a field the declaration already carries is not
 * asked again — asking invites somebody to overwrite an answer they gave on
 * purpose — and that a blank stays out of what is emitted entirely. The server
 * merges this over the original declaration, so a blank sent as `null` would be
 * indistinguishable from an instruction to withdraw an answer.
 */
import { ComponentRef } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { DeclarationField, DeclarationFieldKindEnum, ProjectDeclaration } from '../../api';
import { AnswerNow } from './answer-now';

/** In the shape `/api/config` publishes, cut down to what this component reads. */
const FIELDS: DeclarationField[] = [
  {
    key: 'building_area_sf',
    kind: DeclarationFieldKindEnum.Number,
    pro_label: 'Largest floor area',
    pro_help: 'Gross, per storey.',
    simple_label: 'Biggest floor',
    simple_help: 'How big is the biggest floor?',
    group: 'construction',
    unit: 'sf',
    unlocks: ['DECL.BUILDING_AREA'],
  },
  {
    key: 'stories',
    kind: DeclarationFieldKindEnum.Integer,
    pro_label: 'Storeys above grade',
    pro_help: 'Counted as Table 504.4 counts them.',
    simple_label: 'Floors',
    simple_help: 'How many floors?',
    group: 'construction',
    unit: '',
    unlocks: ['DECL.STORIES'],
  },
  {
    key: 'mixed_occupancy',
    kind: DeclarationFieldKindEnum.Bool,
    pro_label: 'Mixed occupancy',
    pro_help: 'More than one use group.',
    simple_label: 'More than one use',
    simple_help: '',
    group: 'occupancy',
    unit: '',
    unlocks: ['DECL.MIXED_OCCUPANCY'],
  },
  {
    key: 'construction_type',
    kind: DeclarationFieldKindEnum.Enum,
    pro_label: 'Construction type',
    pro_help: 'Table 601.',
    simple_label: 'What it is built of',
    simple_help: '',
    group: 'construction',
    unit: '',
    choices: ['V-A', 'V-B'],
    choice_labels: { 'V-A': 'V-A — protected', 'V-B': 'V-B — unprotected' },
    unlocks: ['DECL.CONSTRUCTION_TYPE'],
  },
];

describe('AnswerNow', () => {
  let fixture: ComponentFixture<AnswerNow>;
  let ref: ComponentRef<AnswerNow>;

  function build(declared: ProjectDeclaration | null = null): void {
    TestBed.configureTestingModule({ imports: [AnswerNow] });
    fixture = TestBed.createComponent(AnswerNow);
    ref = fixture.componentRef;
    ref.setInput('fields', FIELDS);
    ref.setInput('declared', declared);
    fixture.detectChanges();
  }

  function part(): Record<string, unknown> {
    return fixture.componentInstance as unknown as Record<string, unknown>;
  }

  function read(name: string): unknown {
    return (part()[name] as () => unknown)();
  }

  function type(key: string, value: string): void {
    const event = { target: { value } } as unknown as Event;
    (part()['onInput'] as (k: string, e: Event) => void)(key, event);
    fixture.detectChanges();
  }

  function emitted(): ProjectDeclaration | null {
    let seen: ProjectDeclaration | null = null;
    fixture.componentInstance.answered.subscribe((v) => (seen = v));
    (part()['submit'] as () => void)();
    return seen;
  }

  beforeEach(() => {
    TestBed.resetTestingModule();
  });

  // ── which questions get asked ───────────────────────────────────────────
  it('asks every field the rule named when the review declared none of them', () => {
    build(null);
    expect((read('asking') as DeclarationField[]).map((f) => f.key)).toEqual([
      'building_area_sf', 'stories', 'mixed_occupancy', 'construction_type',
    ]);
  });

  it('does not ask again for a value the review already carries', () => {
    // The rule did not stand down for want of *that*, and re-asking invites
    // somebody to overwrite an answer they gave deliberately.
    build({ stories: 2 });
    expect((read('asking') as DeclarationField[]).map((f) => f.key)).not.toContain('stories');
  });

  it('treats a declared false as answered', () => {
    // `false` is an answer. Anything that tested truthiness would ask again for
    // every "no" in the questionnaire.
    build({ mixed_occupancy: false });
    expect((read('asking') as DeclarationField[]).map((f) => f.key)).not.toContain(
      'mixed_occupancy',
    );
  });

  it('treats a declared zero as answered', () => {
    build({ building_area_sf: 0 });
    expect((read('asking') as DeclarationField[]).map((f) => f.key)).not.toContain(
      'building_area_sf',
    );
  });

  // ── what goes back ──────────────────────────────────────────────────────
  it('converts each value to its declared type once, on the way out', () => {
    build(null);
    type('building_area_sf', '4200.5');
    type('stories', '2');
    type('mixed_occupancy', 'true');
    type('construction_type', 'V-B');

    expect(emitted()).toEqual({
      building_area_sf: 4200.5,
      stories: 2,
      mixed_occupancy: true,
      construction_type: 'V-B',
    });
  });

  it('leaves a blank out of the object rather than sending null', () => {
    // The server merges this over what the review already declared. A null
    // would be indistinguishable from "withdraw that answer".
    build(null);
    type('building_area_sf', '4200');
    type('stories', '   ');

    const sent = emitted() as Record<string, unknown> | null;
    expect(sent).not.toBeNull();
    expect(Object.keys(sent!)).toEqual(['building_area_sf']);
  });

  it('emits nothing at all when nothing was typed', () => {
    build(null);
    expect(emitted()).toBeNull();
    expect(read('anyTyped')).toBe(false);
  });

  it('reads false back as false rather than dropping it', () => {
    build(null);
    type('mixed_occupancy', 'false');
    expect(emitted()).toEqual({ mixed_occupancy: false });
  });
});
