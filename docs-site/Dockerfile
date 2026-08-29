# Docs site (Docusaurus) - see README "Docker quickstart". Content is
# bind-mounted from ../docs at run time, so editing a .md file hot-reloads.
FROM node:20-alpine
WORKDIR /site
COPY package.json ./
RUN npm install --no-audit --no-fund
COPY docusaurus.config.js sidebars.js ./
COPY src ./src
EXPOSE 3000
CMD ["npm", "run", "start"]
