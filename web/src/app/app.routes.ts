import { Routes } from '@angular/router';

import { authGuard, ownerGuard, signedOutGuard } from './core/auth-guard';

export const routes: Routes = [
  {
    path: '',
    canActivate: [authGuard],
    loadComponent: () => import('./review/review').then((m) => m.Review),
    title: 'FBC Code Review',
  },
  {
    // The workspace: one review, open, sheet by sheet. A route rather than a
    // panel on the tool page, so it can be linked to and reloaded — and so
    // pdf.js lands in this chunk rather than the initial bundle.
    path: 'review/:id',
    canActivate: [authGuard],
    loadComponent: () => import('./review/workspace').then((m) => m.Workspace),
    title: 'Review · FBC Code Review',
  },
  {
    path: 'admin',
    canActivate: [ownerGuard],
    loadComponent: () => import('./admin/admin').then((m) => m.Admin),
    title: 'Feedback · FBC Code Review',
  },
  {
    path: 'sign-in',
    canActivate: [signedOutGuard],
    loadComponent: () => import('./sign-in/sign-in').then((m) => m.SignIn),
    title: 'Sign in · FBC Code Review',
  },
  { path: '**', redirectTo: '' },
];
