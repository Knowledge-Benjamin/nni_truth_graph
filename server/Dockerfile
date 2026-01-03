# Docker multi-stage build for Node.js server

FROM node:22-alpine

WORKDIR /app

COPY package*.json ./

RUN npm ci --production

COPY . .

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD node -e "require('http').get('http://localhost:3000', (r) => {if (r.statusCode !== 200) throw new Error(r.statusCode)})"

CMD ["node", "index.js"]
