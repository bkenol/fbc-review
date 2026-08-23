/**
 * The questionnaire's two obligations.
 *
 * A blank is not an answer — it must not reach the API as an empty string or a
 * zero, because the engine's whole discipline rests on `null` meaning "nobody
 * said". And no label, enum value or help text may be written here: the schema
 * is served, and a code label that exists in two places will drift.
 */
import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { DeclarationField, DeclarationFieldKindEnum, DeclarationGroup } from '../../api';
import { DeclarationForm } from './declaration-form';

const GROUPS: DeclarationGroup[] = [
  { key: 'occupancy', label: 'Occupancy' },
  { key: 'construction', label: 'Construction & size' },
];

function field(overrides: Partial<DeclarationField> & { key: string }): DeclarationField {
  return {
    kind: DeclarationFieldKindEnum.Text,
    pro_label: `${overrides.key} (code)`,
    pro_help: 'code help',
    simple_label: `${overrides.key} (plain)`,
    simple_help: 'plain help',
    group: 'occupancy',
    unlocks: [],
    ...overrides,
  } as DeclarationField;
}

const FIELDS: DeclarationField[] = [
  field({
    key: 'occupancy_group',
    kind: DeclarationFieldKindEnum.Enum,
    choices: ['A-3', 'B'],
    choice_labels: { 'A-3': 'A-3 — assembly', B: 'B — business' },
    unlocks: ['EGRESS.OCCUPANT_LOAD_COMPUTED', 'HEIGHT_AREA.TABLE_506_AREA'],
  }),
  field({
    key: 'mixed_occupancy',
    kind: DeclarationFieldKindEnum.Bool,
    unlocks: ['DECL.MIXED_OCCUPANCY'],
  }),
  field({
    key: 'building_area_sf',
    kind: DeclarationFieldKindEnum.Number,
    group: 'construction',
    unit: 'sf',
    unlocks: ['HEIGHT_AREA.TABLE_506_AREA'],
  }),
  field({ key: 'stories', kind: DeclarationFieldKindEnum.Integer, group: 'construction' }),
  field({ key: 'jurisdiction' }),
];

const UNLOCKABLE = [
  'DECL.MIXED_OCCUPANCY',
  'EGRESS.OCCUPANT_LOAD_COMPUTED',
  'HEIGHT_AREA.TABLE_506_AREA',
];

function mount() {
  const fixture = TestBed.createComponent(DeclarationForm);
  fixture.componentRef.setInput('fields', FIELDS);
  fixture.componentRef.setInput('groups', GROUPS);
  fixture.componentRef.setInput('unlockable', UNLOCKABLE);
  fixture.detectChanges();
  // The form and the counters are protected on the component, which is right:
  // the template is the only legitimate consumer. Reaching in is what a test
  // does.
  const component = fixture.componentInstance as unknown as {
    form(): { patchValue(v: Record<string, string>): void; reset(): void };
    onEdit(): void;
    answered(): number;
    total(): number;
    standingDown(): number;
    setMode(mode: 'pro' | 'simple'): void;
    mode(): 'pro' | 'simple';
    label(f: DeclarationField): string;
    choiceLabel(f: DeclarationField, choice: string): string;
    clear(): void;
  };
  const answer = (values: Record<string, string>) => {
    component.form().patchValue(values);
    component.onEdit();
    fixture.detectChanges();
  };
  return { fixture, component, answer };
}

describe('DeclarationForm', () => {
  beforeEach(() => {
    localStorage.clear();
    TestBed.configureTestingModule({ imports: [DeclarationForm] });
  });

  it('renders a control for every served field and none of its own', () => {
    const { fixture } = mount();
    const html = fixture.nativeElement as HTMLElement;
    for (const f of FIELDS) {
      expect(html.querySelector(`#decl-${f.key}`), f.key).not.toBeNull();
    }
    expect(html.querySelectorAll('[id^="decl-"]').length).toBe(FIELDS.length);
  });

  it('takes every label and enum value from the schema', () => {
    const { fixture, component } = mount();
    const html = fixture.nativeElement as HTMLElement;
    expect(html.textContent).toContain('occupancy_group (code)');
    expect(html.textContent).toContain('B — business');
    expect(component.label(FIELDS[0])).toBe('occupancy_group (code)');
    expect(component.choiceLabel(FIELDS[0], 'A-3')).toBe('A-3 — assembly');
  });

  it('offers a "not answered" option on every enum, because blank is a real answer', () => {
    const { fixture } = mount();
    const select = (fixture.nativeElement as HTMLElement).querySelector(
      '#decl-occupancy_group',
    ) as HTMLSelectElement;
    expect(select.options[0].value).toBe('');
    expect(select.options[0].text).toContain('Not answered');
  });

  it('gives booleans three states, since a checkbox cannot say "I did not answer"', () => {
    const { fixture } = mount();
    const select = (fixture.nativeElement as HTMLElement).querySelector(
      '#decl-mixed_occupancy',
    ) as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(['', 'true', 'false']);
  });

  it('omits every unanswered field from the submitted declaration', () => {
    const { fixture, answer } = mount();
    answer({ occupancy_group: 'B' });
    expect(fixture.componentInstance.value()).toEqual({ occupancy_group: 'B' });
  });

  it('sends nothing at all when nothing was answered', () => {
    const { fixture } = mount();
    expect(fixture.componentInstance.value()).toEqual({});
  });

  it('converts each answer to the type the schema declares', () => {
    const { fixture, answer } = mount();
    answer({
      occupancy_group: 'B',
      mixed_occupancy: 'false',
      building_area_sf: '15,376',
      stories: '1',
      jurisdiction: '  Lee County, FL  ',
    });
    expect(fixture.componentInstance.value()).toEqual({
      occupancy_group: 'B',
      mixed_occupancy: false,
      building_area_sf: 15376,
      stories: 1,
      jurisdiction: 'Lee County, FL',
    });
  });

  it('counts answers live', () => {
    const { component, answer } = mount();
    expect(component.answered()).toBe(0);
    expect(component.total()).toBe(FIELDS.length);
    answer({ occupancy_group: 'B', stories: '1' });
    expect(component.answered()).toBe(2);
  });

  it('says how many checks are still standing down, from the served metadata', () => {
    const { component, answer } = mount();
    expect(component.standingDown()).toBe(UNLOCKABLE.length);

    // TABLE_506_AREA needs both the occupancy and the area; answering one of
    // them does not unlock it.
    answer({ occupancy_group: 'B' });
    expect(component.standingDown()).toBe(2);

    answer({ building_area_sf: '15376', mixed_occupancy: 'true' });
    expect(component.standingDown()).toBe(0);
  });

  it('clears every answer for the skip path', () => {
    const { fixture, component, answer } = mount();
    answer({ occupancy_group: 'B', stories: '1' });
    component.clear();
    fixture.detectChanges();
    expect(component.answered()).toBe(0);
    expect(fixture.componentInstance.value()).toEqual({});
  });

  it('defaults to the code wording and remembers a switch to plain', () => {
    const { fixture, component } = mount();
    expect(component.mode()).toBe('pro');

    component.setMode('simple');
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('occupancy_group (plain)');
    expect(localStorage.getItem('fbc.declarationVocabulary')).toBe('simple');

    // A fresh mount adopts the remembered choice.
    const again = mount();
    expect(again.component.mode()).toBe('simple');
  });

  it('ties every label to its input and every hint to its field', () => {
    const { fixture } = mount();
    const html = fixture.nativeElement as HTMLElement;
    for (const f of FIELDS) {
      const label = html.querySelector(`label[for="decl-${f.key}"]`);
      expect(label, `${f.key} has no label`).not.toBeNull();
      const control = html.querySelector(`#decl-${f.key}`) as HTMLElement;
      expect(control.getAttribute('aria-describedby')).toBe(`help-${f.key}`);
      expect(html.querySelector(`#help-${f.key}`)).not.toBeNull();
    }
  });

  it('announces the answered count politely', () => {
    const { fixture } = mount();
    const line = (fixture.nativeElement as HTMLElement).querySelector('.tally');
    expect(line?.getAttribute('aria-live')).toBe('polite');
  });
});
