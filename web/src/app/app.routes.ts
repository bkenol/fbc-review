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
    // The training console. Deliberately reachable without a permit set: there
    // is nothing to train on without a document, but there is no reason it has
    // to be a *new* document — every finished review is already here, and the
    // calibration a person has accumulated is a thing to look at on its own.
    path: 'training',
    canActivate: [authGuard],
    loadComponent: () => import('./training/training').then((m) => m.Training),
    title: 'Training · FBC Code Review',
  },
  {
    // The workspace: one review, open, sheet by sheet. A route rather than a
    // panel on the tool page, so it can be linked to and reloaded — and so
    // pdf.js lands in this chunk rather than the initial bundle.
    //
    // `chrome: 'full'` drops the marketing headline and the page gutters. A
    // 36-inch sheet inside a 1080px measure with 96px of clearance above it is
    // a drawing you cannot read, and this is the one screen where the document
    // is the entire point.
    path: 'review/:id',
    canActivate: [authGuard],
    data: { chrome: 'full' },
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
