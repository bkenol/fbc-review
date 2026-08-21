import { HttpErrorResponse } from '@angular/common/http';
import { describe, expect, it } from 'vitest';

import { toFailure } from './review-service';

describe('toFailure', () => {
  it('unwraps the API error envelope', () => {
    const response = new HttpErrorResponse({
      status: 422,
      error: { error: { code: 'raster_pdf', message: 'All 4 sheets are raster images.' } },
    });
    expect(toFailure(response)).toEqual({
      code: 'raster_pdf',
      message: 'All 4 sheets are raster images.',
    });
  });

  it('reports a dropped connection as offline rather than as HTTP 0', () => {
    const response = new HttpErrorResponse({ status: 0, statusText: 'Unknown Error' });
    expect(toFailure(response).code).toBe('offline');
  });

  it('falls back to the status when the body is not an envelope', () => {
    const response = new HttpErrorResponse({
      status: 502,
      statusText: 'Bad Gateway',
      error: '<html>gateway</html>',
    });
    expect(toFailure(response)).toEqual({ code: 'http_502', message: 'Bad Gateway' });
  });

  it('handles a non-HTTP throw', () => {
    expect(toFailure(new Error('boom')).code).toBe('unknown');
  });
});
