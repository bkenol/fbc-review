import { Component, inject } from '@angular/core';

import { AuthService } from '../core/auth';

@Component({
  selector: 'app-sign-in',
  imports: [],
  templateUrl: './sign-in.html',
})
export class SignIn {
  protected readonly auth = inject(AuthService);
}
