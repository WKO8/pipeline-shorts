/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        background: '#09090b',
        foreground: '#f4f4f5',
        card: '#18181b',
        'card-border': '#27272a',
        primary: '#6366f1',
        'primary-hover': '#4f46e5',
        accent: '#06b6d4',
      },
    },
  },
  plugins: [],
};
