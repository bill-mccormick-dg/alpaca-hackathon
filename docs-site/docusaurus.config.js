// Docusaurus site for docs/ - the onboarding + reference site.
// Content lives in ../docs (plain markdown, also readable on GitHub); this
// directory only holds the site scaffolding. Run it with `docker compose up docs`.
// @ts-check

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'AI Day Trader - Long Premium, Short Leash',
  tagline: 'An autonomous options agent on Alpaca\'s MCP server',
  favicon: 'img/favicon.ico',
  url: 'http://localhost:3000',
  baseUrl: '/',
  onBrokenLinks: 'warn',
  markdown: { format: 'md', hooks: { onBrokenMarkdownLinks: 'warn' } }, // plain CommonMark, so GitHub-flavoured docs render unchanged

  presets: [
    [
      'classic',
      /** @type {import('@docusaurus/preset-classic').Options} */
      ({
        docs: {
          path: '../docs',
          routeBasePath: '/',
          sidebarPath: './sidebars.js',
          editUrl: 'https://github.com/bill-mccormick-dg/alpaca-hackathon/edit/main/',
        },
        blog: false,
        theme: { customCss: './src/custom.css' },
      }),
    ],
  ],

  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      navbar: {
        title: 'AI Day Trader',
        items: [
          { to: '/', label: 'Docs', position: 'left' },
          { to: '/strategy', label: 'Strategy', position: 'left' },
          { to: '/operations', label: 'Operations', position: 'left' },
          { href: 'https://github.com/bill-mccormick-dg/alpaca-hackathon', label: 'GitHub', position: 'right' },
        ],
      },
      footer: {
        style: 'dark',
        copyright: `MIT - Alpaca AI Trading Agents Hackathon, Aug 28-Sep 4 2026.`,
      },
      colorMode: { respectPrefersColorScheme: true },
    }),
};

export default config;
