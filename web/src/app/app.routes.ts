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
    path: 'sign-in',
    canActivate: [signedOutGuard],
    loadComponent: () => import('./sign-in/sign-in').then((m) => m.SignIn),
    title: 'Sign in · FBC Code Review',
  },
  { path: '**', redirectTo: '' },
];
