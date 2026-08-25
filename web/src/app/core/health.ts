/**
 * The single call to `/api/healthz`, and the two things the shell reads off it.
 *
 * `/api/healthz` rather than `/healthz`: Firebase Hosting rewrites `/api/**` to
 * Cloud Run and serves `index.html` for everything else, so a fetch of
 * `/healthz` from the browser comes back as the app shell. `ng serve`'s proxy is
 * keyed on `/api` for the same reason. The API answers on both paths; only this
 * one is reachable from the page in every deployment.
 *
 * `fetch` rather than HttpClient, and hand-typed rather than generated: the auth
 * interceptor injects AuthService, AuthService waits on this, and a service
 * cannot cleanly depend on something that depends on it during construction.
 * The shape read here is two fields of a probe that is unauthenticated by
 * design, not a contract the generated client is needed for.
 */
import { Injectable, signal } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class HealthService {
  private readonly _version = signal<string | null>(null);
  private readonly _authRequired = signal<boolean | null>(null);

  /**
   * The version the running API reports — release, channel, build number and
   * commit. Null until the probe answers, and null forever if it cannot: the
   * page then says nothing about the build rather than something wrong about it.
   */
  readonly version = this._version.asReadonly();

  /** Whether this deployment demands a token. Null until the probe answers. */
  readonly authRequired = this._authRequired.asReadonly();

  private readonly probed: Promise<void>;

  constructor() {
    this.probed = this.probe();
  }

  /** Resolves once the probe has answered or failed. Never rejects. */
  whenProbed(): Promise<void> {
    return this.probed;
  }

  private async probe(): Promise<void> {
    try {
      const response = await fetch('api/healthz', { cache: 'no-store' });
      if (!response.ok) return;
      const health = (await response.json()) as { version?: string; auth_required?: boolean };
      if (typeof health.version === 'string' && health.version) {
        this._version.set(health.version);
      }
      if (typeof health.auth_required === 'boolean') {
        this._authRequired.set(health.auth_required);
      }
    } catch {
      // Unreachable server, or an HTML body where JSON was expected. Both leave
      // the signals null, which every caller already treats as "not known".
    }
  }
}
