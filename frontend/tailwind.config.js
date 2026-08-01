/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        ink: { 950: '#0a0e17', 900: '#101725', 850: '#151d2e', 800: '#1c2639', 700: '#26334a', 600: '#3a4a66' },
        accent: { DEFAULT: '#4f8ef7', dim: '#2f6fd8' },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
};
