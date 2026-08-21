/**
 * Attaches the Firebase ID token to API requests. One place, and only here.
 *
 * The prefix test is load-bearing, not tidiness. `withInterceptors` is global,
 * and the results view fetches `findings.json` straight from a Cloud Storage V4
 * signed URL. A signed URL carries its own credentials in the query string, and
 * GCS rejects a request that also presents an `Authorization` header — so a
 * stray token here breaks downloads in a way that looks like a signing bug.
 */
import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { from, switchMap } from 'rxjs';

import { AuthService } from './auth';

function isOwnApi(url: string): boolean {
  if (url.startsWith('/api/')) return true;
  // Absolute form, for a dev proxy or a non-root deployment.
  try {
    return new URL(url, window.location.origin).origin === window.location.origin
      && new URL(url, window.location.origin).pathname.startsWith('/api/');
  } catch {
    return false;
  }
}

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  if (!isOwnApi(req.url)) return next(req);

  const auth = inject(AuthService);
  return from(auth.idToken()).pipe(
    switchMap((token) =>
      next(token ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : req),
    ),
  );
};
