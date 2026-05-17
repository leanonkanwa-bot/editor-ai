# syntax=docker/dockerfile:1.7
# ── Build: compile React app ──────────────────────────────────────────────────
FROM node:20-alpine AS build

WORKDIR /app
COPY package*.json ./
RUN npm ci --prefer-offline

COPY . .
RUN npm run build

# ── Runtime: nginx serves static files + proxies /api ─────────────────────────
FROM nginx:1.27-alpine

# gettext provides envsubst, used to inject BACKEND_URL at container start
RUN apk add --no-cache gettext

RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf.template /etc/nginx/nginx.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

# Substitute only $BACKEND_URL in the nginx config; leave nginx's own
# $host / $uri / $remote_addr etc. untouched (single-quoted argument).
CMD ["/bin/sh", "-c", \
  "envsubst '$BACKEND_URL' < /etc/nginx/nginx.conf.template \
     > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
