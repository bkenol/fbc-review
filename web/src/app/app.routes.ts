import { Routes } from '@angular/router';

import { authGuard, signedOutGuard } from './core/auth-guard';

export const routes: Routes = [
  {
    path: '',
    canActivate: [authGuard],
    loadComponent: () => import('./review/review').then((m) => m.Review),
    title: 'FBC Code Review',
  },
  {
    // Refine analysis: one page carrying both halves of the loop — the reviews
    // you can argue with, and (for an owner) the queue those arguments land in.
    // Deliberately reachable without a permit set: there is nothing to refine
    // without a document, but there is no reason it has to be a *new* document
    // — every finished review is already here, and the calibration a person has
    // accumulated is a thing to look at on its own.
    path: 'refine',
    canActivate: [authGuard],
    loadComponent: () => import('./training/training').then((m) => m.Training),
    title: 'Refine analysis · FBC Code Review',
  },
  {
    // What this page was called before the two halves were joined. Kept for the
    // same reason `/admin` is: the URL was handed out.
    path: 'training',
    redirectTo: 'refine',
    pathMatch: 'full',
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
    // The queue moved onto the refine-analysis page — the two are one loop and
    // a navigation step between a proposal and the decision on it was a step
    // too many. This stays because the URL was handed out, and a dead bookmark
    // is not an improvement on a redirect. Who sees the queue is decided by the
    // component and, where it counts, by the API answering 404 on every
    // `/api/admin/*` path to anybody who is not an owner.
    path: 'admin',
    redirectTo: 'refine',
    pathMatch: 'full',
  },
  {
    path: 'sign-in',
    canActivate: [signedOutGuard],
    loadComponent: () => import('./sign-in/sign-in').then((m) => m.SignIn),
    title: 'Sign in · FBC Code Review',
  },
  { path: '**', redirectTo: '' },
];
