import { defineCollection, z } from 'astro:content';

export const MUNICIPALITIES = [
  'columbus',
  'dublin',
  'westerville',
  'grove-city',
  'gahanna',
  'hilliard',
  'reynoldsburg',
  'worthington',
  'upper-arlington',
  'bexley',
  'franklin-county',
  'delaware-county',
  'licking-county',
] as const;

export const TOPICS = [
  'policy',
  'tax',
  'market',
  'legal',
  'development',
  'event',
  'vendor',
  'utilities',
  'enforcement',
  'incentives',
] as const;

export const CONTENT_TYPES = ['policy', 'news', 'market_data', 'event'] as const;

export const VENDOR_CATEGORIES = [
  'real-estate-agents',
  'property-management',
  'maintenance-repair',
  'legal-compliance',
  'lending-finance',
  'insurance',
  'inspection-assessment',
  'turn-renovation',
  'tax-accounting',
  'design-staging',
] as const;

export const RESOURCE_CATEGORIES = [
  'government',
  'tax',
  'legal',
  'market-data',
  'events',
  'vendors',
] as const;

const items = defineCollection({
  type: 'content',
  schema: z.object({
    title: z.string(),
    summary: z.string().max(600, 'summary should be ~100 words (≤600 chars)'),
    why_it_matters: z.string(),
    source_url: z.string().url(),
    source_domain: z.string(),
    published_at: z.coerce.date(),
    topics: z.array(z.enum(TOPICS)).default([]),
    municipalities: z.array(z.enum(MUNICIPALITIES)).default([]),
    content_type: z.enum(CONTENT_TYPES),
    entities: z.array(z.string()).default([]),
    legislative_status: z.string().optional(),
    classification: z.enum(['new', 'update']).optional(),
    fingerprint: z.string().optional(),
    risk_flags: z.array(z.string()).default([]),
    featured: z.boolean().default(false),
    relevance_score: z.number().min(1).max(10).optional(),
  }),
});

const vendors = defineCollection({
  type: 'content',
  schema: z.object({
    name: z.string(),
    category: z.enum(VENDOR_CATEGORIES),
    sub_categories: z.array(z.string()),
    service_area: z.array(z.enum(MUNICIPALITIES)).default([]),
    website: z.string().url().optional(),
    phone: z.string().optional(),
    email: z.string().email().optional(),
    address: z.string().optional(),
    logo_url: z.string().optional(),
    city_label: z.string().optional(),
    description: z.string(),
    specialty_notes: z.string().optional(),
    years_in_business: z.number().optional(),
    licensed: z.boolean(),
    license_number: z.string().optional(),
    preferred: z.boolean().default(false),
    preferred_reason: z.string().optional(),
    last_verified: z.coerce.date(),
    consent_status: z.enum(['explicit', 'public_listing', 'pending']),
  }).refine(
    (v) => !v.preferred || (v.preferred && v.preferred_reason && v.preferred_reason.length > 0),
    { message: 'preferred_reason is required when preferred is true', path: ['preferred_reason'] },
  ),
});

const cities = defineCollection({
  type: 'content',
  schema: z.object({
    name: z.string(),
    resources: z.array(
      z.object({
        label: z.string(),
        url: z.string().url(),
        category: z.string(),
      }),
    ).default([]),
  }),
});

const resources = defineCollection({
  type: 'content',
  schema: z.object({
    label: z.string(),
    url: z.string().url(),
    category: z.enum(RESOURCE_CATEGORIES),
    municipalities: z.array(z.union([z.enum(MUNICIPALITIES), z.literal('all')])).default(['all']),
    description: z.string(),
  }),
});

export const collections = { items, vendors, cities, resources };
