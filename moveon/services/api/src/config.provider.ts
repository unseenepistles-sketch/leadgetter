import { Injectable } from '@nestjs/common';
import { StaticConfigProvider, type ConfigProvider } from '@moveon/shared';

/**
 * The single place the API gets its rates from. Today it serves the validated
 * JSON seed; when ops gets an editing screen this becomes a Postgres-backed
 * provider with a cache, and nothing else in the codebase changes (§3).
 */
export const CONFIG_PROVIDER = Symbol('CONFIG_PROVIDER');

@Injectable()
export class SeedConfigProvider extends StaticConfigProvider {}

export type { ConfigProvider };
