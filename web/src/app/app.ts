import { Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import {
  ActivatedRoute,
  NavigationEnd,
  Router,
  RouterLink,
  RouterOutlet,
} from '@angular/router';
import { filter, map, startWith } from 'rxjs';

import { AuthService } from './core/auth';
import { HealthService } from './core/health';
import { ReviewService } from './review/review-service';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink],
  templateUrl: './app.html',
})
export class App {
  protected readonly auth = inject(AuthService);
  /** Publishes the running API's version to the masthead and the footer. */
  protected readonly health = inject(HealthService);
  private readonly reviews = inject(ReviewService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  /**
   * Whether the route wants the whole window.
   *
   * Read off the route's own `data` rather than matched against a URL here, so
   * a new full-bleed screen declares itself in `app.routes.ts` next to its
   * path instead of in a list over here that somebody has to remember to
   * update. Walks to the deepest activated child because the flag is set on a
   * leaf.
   */
  protected readonly fullBleed = toSignal(
    this.router.events.pipe(
      filter((event) => event instanceof NavigationEnd),
      startWith(null),
      map(() => {
        let route = this.route;
        while (route.firstChild) route = route.firstChild;
        return route.snapshot.data['chrome'] === 'full';
      }),
    ),
    { initialValue: false },
  );

  /**
   * Whether to offer the feedback queue at all.
   *
   * Convenience only — `ownerGuard` decides the route and the API answers 404
   * on every admin path for anyone else. A link nobody can follow is worse than
   * no link, which is the only reason this is here.
   */
  protected readonly isOwner = computed(
    () => this.reviews.config()?.training?.is_owner ?? false,
  );

  /**
   * Whether this deployment collects feedback at all.
   *
   * The training console needs no permit set, so the masthead link is the
   * whole entry point — but on a deployment with training switched off every
   * route behind it 400s, and a link to that is worse than none.
   */
  protected readonly trainingAvailable = computed(
    () => this.reviews.config()?.training?.enabled ?? false,
  );

  /**
   * The headline carries a measure, per the design system. It used to carry a
   * hand-counted "twelve", which stopped being true the first time a rule was
   * added — so it counts the registry the server publishes instead. Before
   * config arrives it says nothing about the number rather than a stale one.
   */
  protected readonly claim = computed(() => {
    const rules = this.reviews.config()?.rules?.length ?? 0;
    return rules
      ? `${rules} rules, zero model calls, fifteen seconds.`
      : 'Zero model calls, fifteen seconds.';
  });
}
