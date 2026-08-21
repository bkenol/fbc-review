import { Component, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';

import { AuthService } from './core/auth';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet],
  templateUrl: './app.html',
})
export class App {
  protected readonly auth = inject(AuthService);
}
