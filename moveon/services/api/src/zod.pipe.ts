import { BadRequestException, type PipeTransform } from '@nestjs/common';
import type { z } from 'zod';

/** Validation at the edge. Nothing unparsed reaches the pricing engine. */
export class ZodValidationPipe<T> implements PipeTransform {
  constructor(private readonly schema: z.ZodType<T, z.ZodTypeDef, unknown>) {}

  transform(value: unknown): T {
    const result = this.schema.safeParse(value);
    if (!result.success) {
      throw new BadRequestException({
        error: 'invalid_request',
        details: result.error.issues.map((issue) => ({ path: issue.path.join('.'), message: issue.message })),
      });
    }
    return result.data;
  }
}
