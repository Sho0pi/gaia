// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import starlightLlmsTxt from 'starlight-llms-txt';

// https://astro.build/config
export default defineConfig({
	site: 'https://docs.gaia-agent.com',
	integrations: [
		starlight({
			title: 'Gaia',
			logo: { src: './src/assets/gaia-icon.png', alt: 'Gaia' },
			favicon: '/favicon.svg',
			customCss: ['./src/styles/brand.css'],
			// Generates /llms.txt (page index) + /llms-full.txt (full corpus) so an agent — gaia
			// included — can discover and read these docs. See AGENTS.md.
			plugins: [
				starlightLlmsTxt({
					description:
						'Gaia: an AI agent that forges specialist subagents (souls), remembers you, ' +
						'and runs on Telegram, WhatsApp, or your terminal.',
				}),
			],
			social: [
				{ icon: 'github', label: 'GitHub', href: 'https://github.com/Sho0pi/gaia' },
			],
			sidebar: [
				{ label: 'Getting started', slug: 'getting-started' },
				{ label: 'Concepts', items: [{ autogenerate: { directory: 'concepts' } }] },
				{ label: 'Guides', items: [{ autogenerate: { directory: 'guides' } }] },
				{ label: 'Reference', items: [{ autogenerate: { directory: 'reference' } }] },
			],
		}),
	],
});
