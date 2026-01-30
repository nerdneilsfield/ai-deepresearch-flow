/** @type {import('tailwindcss').Config} */
module.exports = {
    darkMode: ['class'],
    content: ['./index.html', './src/**/*.{vue,js,ts,jsx,tsx}'],
  theme: {
  	extend: {
  		colors: {
  			ink: {
  				'100': '#e7ebf0',
  				'200': '#c6ccd5',
  				'300': '#9aa3b2',
  				'400': '#6b7686',
  				'500': '#3b4758',
  				'600': '#2b3543',
  				'700': '#1f2732',
  				'800': '#141a22',
  				'900': '#0b0f14'
  			},
  			mist: {
  				'50': '#f7f8fb',
  				'100': '#eef1f6',
  				'200': '#dfe5ee'
  			},
  			accent: {
  				'400': '#60a5fa',
  				'500': '#3b82f6',
  				'600': '#2563eb',
  				DEFAULT: 'hsl(var(--accent))',
  				foreground: 'hsl(var(--accent-foreground))'
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
  			display: [
  				'Noto Sans SC',
  				'Source Han Sans SC',
  				'PingFang SC',
  				'Microsoft YaHei',
  				'IBM Plex Sans',
  				'Noto Sans',
  				'system-ui',
  				'sans-serif'
  			]
  		},
  		boxShadow: {
  			soft: '0 20px 45px -40px rgba(15, 23, 42, 0.45)'
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
