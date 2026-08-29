import { Component, computed, inject } from '@angular/core';
import { RouterLink, RouterOutlet } from '@angular/router';

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
