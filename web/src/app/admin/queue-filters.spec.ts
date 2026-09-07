/**
 * The queue's two filters, and the counts that drive them.
 *
 * Reported against the live deployment: the counts above the queue said
 * "2 Needs a new component" with no way to see which two, the jump button at
 * the top of the page went to the home page instead of the queue, and the
 * digest button sat directly on top of the state filters below it.
 *
 * The layout half is measured in a browser rather than asserted here — a
 * stylesheet cannot be unit tested into being un-overlapped. What these cover
 * is the behaviour underneath: which request each control makes, and that a
 * count of nothing is not a filter you can land on an empty list with.
 */
import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting, HttpTestingController } from '@angular/common/http/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { TrainingService } from '../training/training-service';

describe('the queue filters', () => {
  let service: TrainingService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(TrainingService);
    http = TestBed.inject(HttpTestingController);
  });

  /** Answer the two requests loadQueue makes, and hand back the queue one. */
  function settle(rows: unknown[] = []): string {
    const list = http.expectOne((r) => r.url.endsWith('/api/admin/feedback'));
    const url = list.request.urlWithParams;
    list.flush({ feedback: rows });
    http.expectOne((r) => r.url.endsWith('/api/admin/overview')).flush({
      open_feedback: rows.length,
      counts: {},
      mail: { configured: false, status: '' },
      github: '',
      assist: '',
      active_version: 0,
      calibrated_rules: 0,
    });
    return url;
  }

  it('starts on what is waiting, with no narrowing', () => {
    service.loadQueue();
    const url = settle();

    expect(url).toContain('state=new');
    expect(url).not.toContain('disposition=');
    expect(service.queueState()).toBe('new');
    expect(service.queueDisposition()).toBe('');
  });

  it('asks the server for one disposition, rather than filtering what it has', () => {
    // The counts are of *open* feedback, and the queue holds one page of one
    // state. Narrowing client-side would show a subset of whatever happened to
    // be loaded, which is not the number on the tile.
    service.loadQueue('new', 'needs_component');
    const url = settle();

    expect(url).toContain('state=new');
    expect(url).toContain('disposition=needs_component');
    expect(service.queueDisposition()).toBe('needs_component');
  });

  it('drops the narrowing when a state is chosen', () => {
    service.loadQueue('new', 'escalate');
    settle();
    expect(service.queueDisposition()).toBe('escalate');

    service.loadQueue('actioned', '');
    const url = settle();

    expect(url).toContain('state=actioned');
    expect(url).not.toContain('disposition=');
    expect(service.queueDisposition()).toBe('');
  });

  it('keeps the filters where both the counts and the buttons can see them', () => {
    // They were a signal inside the Admin component. Two things drive them now
    // — the tiles above the queue and the buttons below it — and a
    // component-local signal would leave the pressed state saying one thing
    // and the rows another.
    expect(service.queueState).toBeDefined();
    expect(service.queueDisposition).toBeDefined();
  });

  it('reports what came back, so a tile and its list cannot disagree', () => {
    service.loadQueue('new', 'escalate');
    settle([{ id: 'a' }, { id: 'b' }]);

    expect(service.queue()).toHaveLength(2);
    expect(service.overview()?.open_feedback).toBe(2);
  });
});
