FROM node:24-alpine@sha256:429bdfbc204b4eabc6438e6d34e4e9d8dd2ca1df8289bcd78cdd3c5db387b7a5

WORKDIR /opt/filesystem-mcp
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund \
    && npm cache clean --force

USER node
ENTRYPOINT ["node", "/opt/filesystem-mcp/node_modules/@modelcontextprotocol/server-filesystem/dist/index.js"]
