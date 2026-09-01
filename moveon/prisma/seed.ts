/**
 * Seeds the database from the same validated config the pricing engine reads.
 *
 * There is deliberately one source of truth: `packages/shared/src/config/*.json`.
 * Seeding copies it into Postgres so ops can edit rates in the console; the
 * engine then reads rows instead of files, with no change to its code (§3).
 */
import { PrismaClient } from '@prisma/client';
import { defaultConfig } from '@moveon/shared';

const prisma = new PrismaClient();

async function main(): Promise<void> {
  const config = defaultConfig;

  // PostGIS must exist before any geography column is created.
  await prisma.$executeRawUnsafe('CREATE EXTENSION IF NOT EXISTS postgis');

  for (const vehicleClass of config.vehicleClasses()) {
    const { code, ...rest } = vehicleClass;
    await prisma.vehicleClass.upsert({ where: { code }, create: { code, ...rest }, update: rest });
  }
  console.log(`vehicle classes: ${config.vehicleClasses().length}`);

  for (const row of config.classPricing()) {
    const { vehicleClass, distanceBands, ...rest } = row;
    const data = { ...rest, distanceBands: distanceBands as object };
    await prisma.classPricing.upsert({
      where: { vehicleClass },
      create: { vehicleClass, ...data },
      update: data,
    });
  }
  console.log(`rate cards: ${config.classPricing().length}`);

  // Global knobs are versioned, so an old quote can always be re-explained.
  const latest = await prisma.pricingConfig.findFirst({ orderBy: { version: 'desc' } });
  const nextVersion = (latest?.version ?? 0) + 1;
  await prisma.pricingConfig.updateMany({ where: { active: true }, data: { active: false } });
  await prisma.pricingConfig.create({
    data: { version: nextVersion, payload: config.pricing() as object, active: true, createdBy: 'seed' },
  });
  console.log(`pricing config: v${nextVersion} active`);

  for (const item of config.catalog()) {
    const { id, ...rest } = item;
    await prisma.catalogItem.upsert({ where: { id }, create: { id, ...rest }, update: rest });
  }
  console.log(`catalog items: ${config.catalog().length}`);

  for (const bundle of config.bundles()) {
    const { id, items, ...rest } = bundle;
    await prisma.catalogBundle.upsert({ where: { id }, create: { id, ...rest }, update: rest });
    // Replace the lines wholesale — a bundle's contents are edited as a unit.
    await prisma.catalogBundleItem.deleteMany({ where: { bundleId: id } });
    await prisma.catalogBundleItem.createMany({
      data: items.map((line) => ({ bundleId: id, itemId: line.itemId, quantity: line.quantity })),
    });
  }
  console.log(`bundles: ${config.bundles().length}`);

  const enabled = config.vehicleClasses().filter((c) => c.enabled).map((c) => c.code);
  console.log(`\nseeded. live classes: ${enabled.join(', ')}`);
}

main()
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  })
  .finally(() => prisma.$disconnect());
