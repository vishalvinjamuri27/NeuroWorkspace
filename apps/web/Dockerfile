FROM node:24.8.0-alpine AS builder
ENV PNPM_HOME=/pnpm
ENV PATH=$PNPM_HOME:$PATH
RUN corepack enable
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web apps/web
COPY packages/shared-types packages/shared-types
# Source is copied in before `pnpm install`, not after, on purpose: this
# used to be a two-stage `dependencies` (install) -> `builder` (COPY source,
# then build) split for better layer-cache reuse on source-only changes.
# Running it for real repeatedly surfaced a genuine BuildKit bug in that
# split -- a `COPY <dir> <dir>` onto a destination that pnpm's install had
# already populated with node_modules symlinks silently orphaned those
# symlinks: `apps/web/node_modules/next` kept existing as a symlink, but its
# target's content-hash suffix no longer matched any directory actually
# extracted under node_modules/.pnpm, breaking `next build` with "Cannot
# find module .../next/dist/bin/next" on some but not all builds sharing an
# otherwise-identical cache. Installing after the full source tree is
# already in place (so no COPY ever lands on top of an existing
# node_modules) avoids it entirely -- confirmed by rebuilding from a pruned
# cache multiple times. See docs/RUNTIME_VALIDATION.md for the full
# reproduction. The tradeoff is a source-only change now invalidates the
# install layer too; a `--mount=type=cache` for the pnpm store keeps that
# cheap regardless.
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store,sharing=locked \
    set -eu; \
    pnpm install --frozen-lockfile; \
    test -e apps/web/node_modules/next/dist/bin/next
ARG NEXT_PUBLIC_API_URL=http://localhost:8000/api
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
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
