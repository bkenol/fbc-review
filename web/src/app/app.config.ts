import {
  ApplicationConfig,
  provideBrowserGlobalErrorListeners,
} from '@angular/core';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { provideRouter } from '@angular/router';

import { provideApi } from './api';
import { authInterceptor } from './core/auth-interceptor';
import { routes } from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes),
    // Functional interceptor, not the class-based HTTP_INTERCEPTORS multi
    // provider. The generated API services use HttpClient, so this covers them
    // with no per-call wiring.
    provideHttpClient(withInterceptors([authInterceptor])),
    // Empty base path keeps requests relative: same-origin behind the Firebase
    // Hosting rewrite in production, and through ng serve's proxy in dev.
    provideApi(''),
  ],
};
