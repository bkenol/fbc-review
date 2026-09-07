/**
 * Route guard for the tool page.
 *
 * Waits for the SDK to restore a session before deciding. Without that wait a
 * page reload bounces a signed-in user to the sign-in screen, because
 * onAuthStateChanged has not fired yet on the first tick.
 *
 * This is a convenience, not a control. Every route on the API verifies the
 * token and checks the allowlist server-side; a guard in the browser only
 * decides which screen to render.
 */
import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { AuthService } from './auth';

export const authGuard: CanActivateFn = async () => {
  const auth = inject(AuthService);
  const router = inject(Router);

  await auth.whenReady();
  return auth.user() !== null ? true : router.createUrlTree(['/sign-in']);
};

/** Keeps a signed-in user off the sign-in screen. */
export const signedOutGuard: CanActivateFn = async () => {
  const auth = inject(AuthService);
  const router = inject(Router);

  await auth.whenReady();
  return auth.user() === null ? true : router.createUrlTree(['/']);
};
