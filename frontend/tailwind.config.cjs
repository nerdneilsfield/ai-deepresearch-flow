/** @type {import('tailwindcss').Config} */
module.exports = {
    darkMode: ['class'],
    content: ['./index.html', './src/**/*.{vue,js,ts,jsx,tsx}'],
  theme: {
  	extend: {
  		colors: {
  			// Scholarly slate neutrals — cooler and more professional
  			ink: {
  				'50': '#f8fafc',
  				'100': '#f1f5f9',
  				'200': '#e2e8f0',
  				'300': '#cbd5e1',
  				'400': '#94a3b8',
  				'500': '#64748b',
  				'600': '#475569',
  				'700': '#334155',
  				'800': '#1e293b',
  				'900': '#0f172a'
  			},
  			// Page background — cool scholarly white
  			mist: {
  				'50': '#f8fafc',
  				'100': '#f1f5f9',
  				'200': '#e2e8f0'
  			},
  			// Primary — Deep navy blue (scholarly authority)
  			accent: {
  				'50': '#eff6ff',
  				'100': '#dbeafe',
  				'200': '#bfdbfe',
  				'400': '#4589ff',
  				'500': '#1d6ff3',
  				'600': '#0f62fe',
  				'700': '#0043ce',
  				'800': '#002d9c',
  				'900': '#001d6c',
  				DEFAULT: 'hsl(var(--accent))',
  				foreground: 'hsl(var(--accent-foreground))'
  			},
  			// Scientific accent — Teal (analytical, fresh, complementary to navy)
  			sci: {
  				'50': '#f0fdfa',
  				'100': '#ccfbf1',
  				'200': '#99f6e4',
  				'400': '#2dd4bf',
  				'500': '#14b8a6',
  				'600': '#0d9488',
  				'700': '#0f766e',
  				'800': '#115e59',
  				'900': '#134e4a',
  			},
  			background: 'hsl(var(--background))',
  			foreground: 'hsl(var(--foreground))',
  			card: {
  				DEFAULT: 'hsl(var(--card))',
  				foreground: 'hsl(var(--card-foreground))'
  			},
  			popover: {
  				DEFAULT: 'hsl(var(--popover))',
  				foreground: 'hsl(var(--popover-foreground))'
  			},
  			primary: {
  				DEFAULT: 'hsl(var(--primary))',
  				foreground: 'hsl(var(--primary-foreground))'
  			},
  			secondary: {
  				DEFAULT: 'hsl(var(--secondary))',
  				foreground: 'hsl(var(--secondary-foreground))'
  			},
  			muted: {
  				DEFAULT: 'hsl(var(--muted))',
  				foreground: 'hsl(var(--muted-foreground))'
  			},
  			destructive: {
  				DEFAULT: 'hsl(var(--destructive))',
  				foreground: 'hsl(var(--destructive-foreground))'
  			},
  			border: 'hsl(var(--border))',
  			input: 'hsl(var(--input))',
  			ring: 'hsl(var(--ring))',
  			chart: {
  				'1': 'hsl(var(--chart-1))',
  				'2': 'hsl(var(--chart-2))',
  				'3': 'hsl(var(--chart-3))',
  				'4': 'hsl(var(--chart-4))',
  				'5': 'hsl(var(--chart-5))'
  			}
  		},
  		fontFamily: {
  			// Self-hosted: Inter (sans) for UI/body, Source Serif 4 (serif) for titles
  			sans: [
  				'Inter Variable',
  				'Noto Sans SC',
  				'system-ui',
  				'-apple-system',
  				'sans-serif'
  			],
  			serif: [
  				'"Source Serif 4 Variable"',
  				'Noto Serif SC',
  				'Georgia',
  				'serif'
  			],
  			mono: [
  				'"JetBrains Mono Variable"',
  				'ui-monospace',
  				'SFMono-Regular',
  				'monospace'
  			],
  			// Backwards-compat alias used by existing `font-display` utility
  			display: [
  				'Inter Variable',
  				'Noto Sans SC',
  				'system-ui',
  				'sans-serif'
  			]
  		},
  		boxShadow: {
  			soft: '0 20px 45px -40px rgba(15, 23, 42, 0.45)',
  			// Flat card — sidebars, secondary panels
  			card: '0 1px 2px 0 rgba(15, 23, 42, 0.04), 0 1px 3px 0 rgba(15, 23, 42, 0.04)',
  			// Elevated — primary content cards (search results, main action area)
  			elevated: '0 4px 6px -1px rgba(15, 23, 42, 0.06), 0 2px 4px -2px rgba(15, 23, 42, 0.05)',
  			'elevated-lg': '0 10px 25px -5px rgba(15, 23, 42, 0.10), 0 8px 10px -6px rgba(15, 23, 42, 0.06)',
  			// Floating layers — dropdowns, popovers, tooltips
  			popover: '0 12px 32px -8px rgba(15, 23, 42, 0.18)',
  			// Legacy alias kept for existing `shadow-card-hover` references
  			'card-hover': '0 4px 16px -4px rgba(15, 23, 42, 0.1), 0 2px 4px -2px rgba(15, 23, 42, 0.05)',
  		},
  		borderRadius: {
  			lg: 'var(--radius)',
  			md: 'calc(var(--radius) - 2px)',
  			sm: 'calc(var(--radius) - 4px)'
  		}
  	}
  },
  plugins: [require('@tailwindcss/typography'), require("tailwindcss-animate")],
}
