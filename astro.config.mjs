import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind';

export default defineConfig({
  site: 'https://bric.news',
  integrations: [
    tailwind({ applyBaseStyles: false }),
  ],
  server: { port: 4321, host: true },
  trailingSlash: 'ignore',
});
