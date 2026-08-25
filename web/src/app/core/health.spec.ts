import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HealthService } from './health';

/** Stands in for the one `fetch` the service makes on construction. */
function respond(body: unknown, { ok = true } = {}) {
  return vi.fn(async () => ({
    ok,
    json: async () => body,
  })) as unknown as typeof fetch;
}

describe('HealthService', () => {
  const realFetch = globalThis.fetch;

  beforeEach(() => TestBed.configureTestingModule({}));
  afterEach(() => {
    globalThis.fetch = realFetch;
    vi.restoreAllMocks();
  });

  it('reads the version and the auth requirement off one call', async () => {
    globalThis.fetch = respond({ version: '1.0.0-alpha.412+3f1c9ab', auth_required: false });

    const health = TestBed.inject(HealthService);
    await health.whenProbed();

    expect(health.version()).toBe('1.0.0-alpha.412+3f1c9ab');
    expect(health.authRequired()).toBe(false);
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
  });

  it('asks /api/healthz, which is the only path Hosting rewrites to the API', () => {
    // Fetching /healthz from the page returns index.html on Firebase Hosting
    // and under `ng serve`, whose proxy is keyed on /api. Getting this wrong
    // fails silently — an HTML body where JSON was expected.
    globalThis.fetch = respond({ version: '1.0.0-alpha' });

    TestBed.inject(HealthService);

    expect(globalThis.fetch).toHaveBeenCalledWith('api/healthz', { cache: 'no-store' });
  });

  it('stays silent rather than guessing when the server cannot be reached', async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    }) as unknown as typeof fetch;

    const health = TestBed.inject(HealthService);
    await expect(health.whenProbed()).resolves.toBeUndefined();

    expect(health.version()).toBeNull();
    expect(health.authRequired()).toBeNull();
  });

  it('stays silent on a non-OK response', async () => {
    globalThis.fetch = respond({ version: '1.0.0-alpha' }, { ok: false });

    const health = TestBed.inject(HealthService);
    await health.whenProbed();

    expect(health.version()).toBeNull();
  });

  it('ignores a body that answers with the wrong shape', async () => {
    // An HTML app shell parsed as JSON, or a field renamed away. Either way the
    // masthead says nothing rather than something wrong.
    globalThis.fetch = respond({ version: 42, auth_required: 'yes' });

    const health = TestBed.inject(HealthService);
    await health.whenProbed();

    expect(health.version()).toBeNull();
    expect(health.authRequired()).toBeNull();
  });
});
