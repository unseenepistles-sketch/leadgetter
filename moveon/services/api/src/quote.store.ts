import { Injectable } from '@nestjs/common';
import type { ParsedQuoteRequest, Quote } from '@moveon/shared';

export interface StoredQuote {
  quote: Quote;
  request: ParsedQuoteRequest;
}

/**
 * In-memory for Milestone 1 — the engine is what this milestone is proving.
 * Milestone 2 swaps this for the Postgres `Quote` table in `prisma/schema.prisma`,
 * which already has the columns.
 */
@Injectable()
export class QuoteStore {
  private readonly quotes = new Map<string, StoredQuote>();

  save(quote: Quote, request: ParsedQuoteRequest): void {
    this.quotes.set(quote.quoteId, { quote, request });
  }

  get(id: string): StoredQuote | undefined {
    return this.quotes.get(id);
  }
}
