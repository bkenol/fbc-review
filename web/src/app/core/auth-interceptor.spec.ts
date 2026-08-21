import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { AuthService } from './auth';
import { authInterceptor } from './auth-interceptor';

class StubAuth {
  token: string | null = 'test-id-token';
  async idToken(): Promise<string | null> {
    return this.token;
  }
}

describe('authInterceptor', () => {
  let http: HttpClient;
  let controller: HttpTestingController;
  let auth: StubAuth;

  beforeEach(() => {
    auth = new StubAuth();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        { provide: AuthService, useValue: auth },
      ],
    });
    http = TestBed.inject(HttpClient);
    controller = TestBed.inject(HttpTestingController);
  });

  // The interceptor awaits idToken() before dispatching — that is the point of
  // it, since the SDK may need to refresh — so the request is not in flight
  // synchronously. Let the microtask queue drain first.
  const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

  it('attaches the bearer token to API requests', async () => {
    http.get('/api/config').subscribe();
    await settle();
    const request = controller.expectOne('/api/config');
    expect(request.request.headers.get('Authorization')).toBe('Bearer test-id-token');
    request.flush({});
  });

  it('never attaches a token to a Cloud Storage signed URL', async () => {
    // The signed URL carries its own credentials in the query string. GCS
    // rejects a request that also presents an Authorization header, so a stray
    // token here breaks every download and looks like a signing bug.
    const signed =
      'https://storage.googleapis.com/omniflex-fbc-review/outputs/abc/findings.json' +
      '?X-Goog-Algorithm=GOOG4-RSA-SHA256&X-Goog-Signature=deadbeef';

    http.get(signed).subscribe();
    const request = controller.expectOne(signed);
    expect(request.request.headers.has('Authorization')).toBe(false);
    request.flush({});
  });

  it('leaves other origins alone', async () => {
    http.get('https://example.com/thing').subscribe();
    const request = controller.expectOne('https://example.com/thing');
    expect(request.request.headers.has('Authorization')).toBe(false);
    request.flush({});
  });

  it('sends the request unchanged when there is no signed-in user', async () => {
    auth.token = null;
    http.get('/api/config').subscribe();
    await settle();
    const request = controller.expectOne('/api/config');
    expect(request.request.headers.has('Authorization')).toBe(false);
    request.flush({});
  });
});
