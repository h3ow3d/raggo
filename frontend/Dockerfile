# syntax=docker/dockerfile:1

# ---- build stage -----------------------------------------------------------
FROM node:20-alpine AS build
WORKDIR /app

# Install dependencies first for better layer caching.
COPY package.json ./
# package-lock.json may or may not exist; copy if present.
COPY package-lock.json* ./
RUN npm install --no-audit --no-fund

# Copy the rest of the source and build.
COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

# ---- runtime stage ---------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

# Replace the default site config with one that serves the SPA and proxies
# `/api/*` to the backend container on the internal Docker network.
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Built static assets.
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# Use the stock nginx CMD.
