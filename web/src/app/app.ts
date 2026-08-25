import { Component, computed, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';

import { AuthService } from './core/auth';
import { HealthService } from './core/health';
import { ReviewService } from './review/review-service';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet],
  templateUrl: './app.html',
})
export class App {
  protected readonly auth = inject(AuthService);
  /** Publishes the running API's version to the masthead and the footer. */
  protected readonly health = inject(HealthService);
  private readonly reviews = inject(ReviewService);

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
