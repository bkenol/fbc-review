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

import { ReviewService } from '../review/review-service';
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

/**
 * Keeps a non-owner off the feedback queue.
 *
 * As with `authGuard`, this decides which screen to render and nothing more.
 * Ownership is enforced on every `/api/admin/*` route server-side, where it
 * answers 404 rather than 403 — the existence of an admin surface is not
 * something to confirm to somebody who is not on it. This guard exists so a
 * non-owner who types the URL gets the tool page instead of an empty console
 * full of failed requests.
 */
export const ownerGuard: CanActivateFn = async () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  const reviews = inject(ReviewService);

  await auth.whenReady();
  if (auth.user() === null) return router.createUrlTree(['/sign-in']);

  const config = await reviews.whenConfigured();
  return config?.training?.is_owner ? true : router.createUrlTree(['/']);
};
