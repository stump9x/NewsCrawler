# syntax=docker/dockerfile:1.7

# Build stage
FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN --mount=type=cache,id=newscrawler-npm,target=/root/.npm \
    npm ci --no-audit --no-fund

COPY . .
ARG VITE_API_BASE_URL=""
ARG VITE_NOTEBOOK_URL=""
ARG VITE_NOTEBOOK_PORT="8502"
ARG VITE_NOTEBOOK_CHAT_V2="false"
ARG VITE_NOTEBOOK_CHAT_HEDGE_DELAY_MS="1400"
ARG VITE_NOTEBOOK_CHAT_CLOUD_TIMEOUT_MS="28000"
ARG VITE_NOTEBOOK_CHAT_LOCAL_TIMEOUT_MS="45000"
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_NOTEBOOK_URL=$VITE_NOTEBOOK_URL
ENV VITE_NOTEBOOK_PORT=$VITE_NOTEBOOK_PORT
ENV VITE_NOTEBOOK_CHAT_V2=$VITE_NOTEBOOK_CHAT_V2
ENV VITE_NOTEBOOK_CHAT_HEDGE_DELAY_MS=$VITE_NOTEBOOK_CHAT_HEDGE_DELAY_MS
ENV VITE_NOTEBOOK_CHAT_CLOUD_TIMEOUT_MS=$VITE_NOTEBOOK_CHAT_CLOUD_TIMEOUT_MS
ENV VITE_NOTEBOOK_CHAT_LOCAL_TIMEOUT_MS=$VITE_NOTEBOOK_CHAT_LOCAL_TIMEOUT_MS
# Production builds skip the test suite by default. Set RUN_FRONTEND_TESTS=1
# when a full test run is desired; CI remains the right place for that gate.
ARG RUN_FRONTEND_TESTS="0"
RUN if [ "$RUN_FRONTEND_TESTS" = "1" ]; then npm test; fi \
 && npm run build \
 && npm cache clean --force \
 && rm -rf node_modules/.cache node_modules/.vite coverage *.tsbuildinfo /root/.npm

# Serve stage
FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
