import { Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';

/**
 * All impurity, in one injectable place. The pricing engine takes `now` and the
 * quote id as arguments precisely so it never has to reach for either.
 */
@Injectable()
export class Clock {
  now(): Date {
    return new Date();
  }

  newId(prefix: string): string {
    return `${prefix}_${randomUUID()}`;
  }
}
