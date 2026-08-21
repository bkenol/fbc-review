/**
 * Firebase Authentication, via the modular JS SDK directly.
 *
 * Not @angular/fire: its stable line trails Angular's release train by a major
 * version and Firebase documents it as maintained by Googlers rather than as a
 * supported Firebase product. The auth path is not the place to take that.
 *
 * The SDK's `User` is bridged into a signal, so templates react without any
 * manual change detection — this app is zoneless.
 */
import { Injectable, computed, isDevMode, signal } from '@angular/core';
import { FirebaseApp, getApps, initializeApp } from 'firebase/app';
import {
  Auth,
  GoogleAuthProvider,
  User,
  getAuth,
  onAuthStateChanged,
  signInWithPopup,
  signOut,
} from 'firebase/auth';

import { FIREBASE_CONFIG, isFirebaseConfigured } from './firebase-config';

/** What the UI needs to distinguish. The middle state is the one that is easy
 *  to get wrong: signed in with a real Google account, and still not allowed. */
export type AccessState = 'starting' | 'signed-out' | 'checking' | 'allowed' | 'not-allowed';

/** Stand-in for `ng serve` against a dev API. See the constructor. */
const LOCAL_DEV_USER = {
  email: 'local-dev@localhost',
  getIdToken: async () => null,
} as unknown as User;

@Injectable({ providedIn: 'root' })
export class AuthService {
  private app: FirebaseApp | null = null;
  private auth: Auth | null = null;

  private readonly _user = signal<User | null>(null);
  private readonly _ready = signal(false);
  private readonly _denied = signal<string | null>(null);
  private readonly _signingIn = signal(false);
  private readonly _error = signal<string | null>(null);

  /** The signed-in Firebase user, or null. */
  readonly user = this._user.asReadonly();
  /** False until the first onAuthStateChanged callback has run. */
  readonly ready = this._ready.asReadonly();
  /** The API's 403 message, when the account is not on the allowlist. */
  readonly denied = this._denied.asReadonly();
  readonly signingIn = this._signingIn.asReadonly();
  readonly error = this._error.asReadonly();

  readonly configured = isFirebaseConfigured();
  readonly email = computed(() => this._user()?.email ?? null);

  readonly state = computed<AccessState>(() => {
    if (!this._ready()) return 'starting';
    if (!this._user()) return 'signed-out';
    if (this._denied()) return 'not-allowed';
    return 'allowed';
  });

  private readyResolvers: Array<() => void> = [];

  constructor() {
    if (!this.configured) {
      // Nothing to connect to yet.
      //
      // Under `ng serve` against a dev API, stand in a local user so the tool
      // page is reachable and can actually be used. This is not a security
      // decision: the server verifies the token and checks the allowlist on
      // every route, so a client that claims to be signed in simply gets 401s
      // from a real deployment. isDevMode() is false in any `ng build` output,
      // and a real deployment has Firebase configured so this branch is dead
      // code there twice over.
      if (isDevMode()) {
        this._user.set(LOCAL_DEV_USER);
      }
      this._ready.set(true);
      return;
    }

    this.app = getApps().length ? getApps()[0] : initializeApp(FIREBASE_CONFIG);
    this.auth = getAuth(this.app);

    onAuthStateChanged(this.auth, (user) => {
      this._user.set(user);
      if (!user) this._denied.set(null);
      if (!this._ready()) {
        this._ready.set(true);
        this.readyResolvers.forEach((resolve) => resolve());
        this.readyResolvers = [];
      }
    });
  }

  /** Resolves once the SDK has restored (or ruled out) a session. Route guards
   *  must wait for this, or a reload bounces a signed-in user to sign-in. */
  whenReady(): Promise<void> {
    if (this._ready()) return Promise.resolve();
    return new Promise<void>((resolve) => this.readyResolvers.push(resolve));
  }

  async signIn(): Promise<void> {
    if (!this.auth) {
      this._error.set('This deployment has no Firebase project configured yet.');
      return;
    }
    this._signingIn.set(true);
    this._error.set(null);
    try {
      const provider = new GoogleAuthProvider();
      provider.setCustomParameters({ prompt: 'select_account' });
      await signInWithPopup(this.auth, provider);
    } catch (error: unknown) {
      this._error.set(describeSignInError(error));
    } finally {
      this._signingIn.set(false);
    }
  }

  async signOut(): Promise<void> {
    this._denied.set(null);
    if (this.auth) await signOut(this.auth);
  }

  /**
   * A current ID token, fetched per request rather than cached here. The SDK
   * keeps its own cache and refreshes shortly before expiry; caching it again
   * in application code is how you end up sending an expired one.
   */
  async idToken(): Promise<string | null> {
    const user = this._user();
    if (!user) return null;
    try {
      return await user.getIdToken();
    } catch {
      return null;
    }
  }

  /** Called by the API error handler when the server returns 403. */
  markNotAllowed(message: string): void {
    this._denied.set(message);
  }
}

function describeSignInError(error: unknown): string | null {
  const code = (error as { code?: string } | null)?.code ?? '';
  switch (code) {
    case 'auth/popup-closed-by-user':
    case 'auth/cancelled-popup-request':
      return null; // deliberate, not a failure
    case 'auth/popup-blocked':
      return 'Your browser blocked the sign-in window. Allow pop-ups for this site and try again.';
    case 'auth/unauthorized-domain':
      return 'This domain is not in the Firebase project’s authorised domains.';
    case 'auth/network-request-failed':
      return 'Could not reach the sign-in service. Check your connection and try again.';
    default:
      return 'Sign-in failed. Try again.';
  }
}
