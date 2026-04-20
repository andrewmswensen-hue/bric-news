/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}'],
  theme: {
    extend: {
      colors: {
        brick:    '#972E28',
        charcoal: '#1F1F1F',
        bone:     '#FAF9F6',
        'bone-2': '#FCFBF8',
        slate:    '#4A4A4A',
        gold:     '#D4AF37',
        rule:     '#E5E0D8',
      },
      fontFamily: {
        heading: ['"Montserrat Variable"', 'Montserrat', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        body:    ['"Inter Variable"', 'Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono:    ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
      },
      maxWidth: {
        content: '72rem',
        prose: '42rem',
      },
    },
  },
  plugins: [],
};
