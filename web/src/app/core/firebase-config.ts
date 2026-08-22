/**
 * Firebase web configuration.
 *
 * These values are public by design — they identify the project, they do not
 * authorise anything. Access is decided server-side by verifying the ID token
 * and checking the email allowlist (webapp/auth.py). Committing them is normal
 * Firebase practice and is not a secret leak.
 *
 * Written by scripts/provision.sh from `firebase apps:sdkconfig`. Re-running
 * provisioning regenerates it; hand edits will be overwritten.
 */
export interface FirebaseWebConfig {
  apiKey: string;
  authDomain: string;
  projectId: string;
  appId: string;
  storageBucket?: string;
  messagingSenderId?: string;
}

const PLACEHOLDER = 'REPLACE_ME';

export const FIREBASE_CONFIG: FirebaseWebConfig = {
  apiKey: PLACEHOLDER,
  authDomain: PLACEHOLDER,
  projectId: PLACEHOLDER,
  appId: PLACEHOLDER,
};

/**
 * False until the project exists. The app then says so plainly instead of
 * throwing an opaque Firebase error into the console on first paint.
 */
export function isFirebaseConfigured(): boolean {
  return (
    FIREBASE_CONFIG.apiKey !== PLACEHOLDER &&
    FIREBASE_CONFIG.authDomain !== PLACEHOLDER &&
    FIREBASE_CONFIG.projectId !== PLACEHOLDER
  );
}
