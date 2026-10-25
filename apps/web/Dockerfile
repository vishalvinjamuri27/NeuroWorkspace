FROM node:24.8.0-alpine AS builder
ENV PNPM_HOME=/pnpm
ENV PATH=$PNPM_HOME:$PATH
RUN corepack enable
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web apps/web
COPY packages/shared-types packages/shared-types
# Install after copying source, not before: COPYing a directory onto one that
# pnpm already filled with node_modules symlinks intermittently orphaned them
# under BuildKit ("Cannot find module .../next/dist/bin/next"). The pnpm store
# cache mount keeps the resulting reinstall on source changes cheap.
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store,sharing=locked \
    set -eu; \
    pnpm install --frozen-lockfile; \
    test -e apps/web/node_modules/next/dist/bin/next
ARG NEXT_PUBLIC_API_URL=http://localhost:8000/api
ARG NEXT_PUBLIC_REPOSITORY_URL=
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_REPOSITORY_URL=$NEXT_PUBLIC_REPOSITORY_URL
RUN pnpm --filter @neuroworkspace/web build
# Next's standalone file tracer can retain pnpm's @swc/helpers symlinks while
# copying only the CommonJS helper files. The production server loads the ESM
# helpers on Linux, so make that traced package complete before the runtime
# stage copies the standalone tree.
RUN set -eu; \
    helper_source="$(find /app/node_modules/.pnpm -path '*/node_modules/@swc/helpers' -type d -print -quit)"; \
    helper_target="$(find /app/apps/web/.next/standalone/node_modules/.pnpm -path '*/node_modules/@swc/helpers' -type d -print -quit)"; \
    test -n "$helper_source"; \
    test -n "$helper_target"; \
    cp -R "$helper_source/." "$helper_target/"

FROM node:24.8.0-alpine AS runtime
ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=3000
WORKDIR /app
RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs
COPY --from=builder --chown=nextjs:nodejs /app/apps/web/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/apps/web/.next/static ./apps/web/.next/static
USER nextjs
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
