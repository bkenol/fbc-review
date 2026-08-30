import { ComponentRef } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { FeedbackAspect, FeedbackSubject, FeedbackVerdictPolarityEnum } from '../api';
import { FeedbackDraft, FeedbackPanel } from './feedback-panel';

/** A cut-down taxonomy in the shape /api/config publishes. */
const ASPECTS: FeedbackAspect[] = [
  {
    key: 'severity',
    label: 'How much it matters',
    help: 'Is this at the right level?',
    subject: FeedbackSubject.Finding,
    required: false,
    verdicts: [
      { key: 'right', label: 'Right level', help: '', polarity: FeedbackVerdictPolarityEnum.Good },
      { key: 'overstated', label: 'Overstated', help: '', polarity: FeedbackVerdictPolarityEnum.Defect },
    ],
  },
  {
    key: 'gap',
    label: 'What was missed',
    help: 'What should this have caught?',
    subject: FeedbackSubject.Coverage,
    required: true,
    verdicts: [{ key: 'missed_violation', label: 'A violation', help: '', polarity: FeedbackVerdictPolarityEnum.Defect }],
  },
];

describe('FeedbackPanel', () => {
  let fixture: ComponentFixture<FeedbackPanel>;
  let ref: ComponentRef<FeedbackPanel>;

  beforeEach(() => {
    TestBed.configureTestingModule({ imports: [FeedbackPanel] });
    fixture = TestBed.createComponent(FeedbackPanel);
    ref = fixture.componentRef;
    ref.setInput('aspects', ASPECTS);
    ref.setInput('subject', FeedbackSubject.Finding);
    fixture.detectChanges();
  });

  function panel(): Record<string, unknown> {
    return fixture.componentInstance as unknown as Record<string, unknown>;
  }

  function call(name: string, ...args: unknown[]): unknown {
    return (panel()[name] as (...a: unknown[]) => unknown)(...args);
  }

  function read(name: string): unknown {
    return (panel()[name] as () => unknown)();
  }

  it('asks only the questions that belong to the subject in hand', () => {
    expect((read('asked') as FeedbackAspect[]).map((a) => a.key)).toEqual(['severity']);

    ref.setInput('subject', FeedbackSubject.Coverage);
    fixture.detectChanges();
    expect((read('asked') as FeedbackAspect[]).map((a) => a.key)).toEqual(['gap']);
  });

  it('will not send an empty submission', () => {
    expect(read('ready')).toBe(false);
  });

  it('lets an answer be taken back', () => {
    // An aspect nobody has an opinion about must stay unanswered. A form that
    // cannot be un-answered collects a first guess and records it as judgement.
    call('choose', 'severity', 'overstated');
    expect(call('chosen', 'severity')).toBe('overstated');
    expect(read('ready')).toBe(true);

    call('choose', 'severity', 'overstated');
    expect(call('chosen', 'severity')).toBe('');
    expect(read('ready')).toBe(false);
  });

  it('accepts a comment on its own, because prose is a real report', () => {
    (panel()['comment'] as { set(v: string): void }).set('the RTU row is merged');
    expect(read('ready')).toBe(true);
  });

  it('holds a submission back until a required aspect is answered', () => {
    ref.setInput('subject', FeedbackSubject.Coverage);
    fixture.detectChanges();

    (panel()['comment'] as { set(v: string): void }).set('something is missing here');
    expect(read('ready')).toBe(false);

    call('choose', 'gap', 'missed_violation');
    expect(read('ready')).toBe(true);
  });

  it('emits what was answered, with the comment trimmed', () => {
    const sent: FeedbackDraft[] = [];
    fixture.componentInstance.submitted.subscribe((draft) => sent.push(draft));

    call('choose', 'severity', 'overstated');
    (panel()['comment'] as { set(v: string): void }).set('  see 1006.2.1  ');
    call('send');

    expect(sent).toEqual([
      { answers: { severity: 'overstated' }, comment: 'see 1006.2.1' },
    ]);
  });

  it('sends nothing while a submission is in flight', () => {
    const sent: FeedbackDraft[] = [];
    fixture.componentInstance.submitted.subscribe((draft) => sent.push(draft));

    call('choose', 'severity', 'overstated');
    ref.setInput('busy', true);
    fixture.detectChanges();
    call('send');

    expect(sent).toEqual([]);
  });
});
