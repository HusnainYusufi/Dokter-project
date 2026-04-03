# =============================================================================
# Stage 1 — deps
# Install production + dev deps (needed for next build)
# =============================================================================
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# =============================================================================
# Stage 2 — builder
# =============================================================================
FROM node:20-alpine AS builder
WORKDIR /app

ARG NEXTAUTH_URL
ARG NEXTAUTH_SECRET
ARG ADMIN_EMAIL
ARG ADMIN_PASSWORD
ARG NEXT_PUBLIC_API_URL

COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN mkdir -p public

ENV NEXT_TELEMETRY_DISABLED=1 \
    NEXTAUTH_URL=${NEXTAUTH_URL} \
    NEXTAUTH_SECRET=${NEXTAUTH_SECRET} \
    ADMIN_EMAIL=${ADMIN_EMAIL} \
    ADMIN_PASSWORD=${ADMIN_PASSWORD} \
    NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}

RUN npm run build

# =============================================================================
# Stage 3 — runtime (standalone output)
# =============================================================================
FROM node:20-alpine AS runtime

RUN addgroup -S appgroup && adduser -S appuser -G appgroup

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

# Railway injects runtime env vars into the container. Build-time args above keep
# the Next.js build aligned with the deployed values, especially NEXT_PUBLIC_*.
# next build --output standalone produces a self-contained server
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

RUN chown -R appuser:appgroup /app
USER appuser

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD wget -qO- http://localhost:3000/ || exit 1

CMD ["node", "server.js"]
