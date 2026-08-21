export * from './config.api';
import { ConfigApi } from './config.api';
export * from './health.api';
import { HealthApi } from './health.api';
export * from './reviews.api';
import { ReviewsApi } from './reviews.api';
export const APIS = [ConfigApi, HealthApi, ReviewsApi];
