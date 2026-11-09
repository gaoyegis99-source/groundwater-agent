FROM platform-base-node AS web
WORKDIR /workspace
ENV PLATFORM_WEB_BUILD_ROOT=/workspace/.build
COPY package.json package-lock.json .npmrc ./
COPY packages/ui-boundary/package.json ./packages/ui-boundary/package.json
COPY packages/operator-ui/package.json ./packages/operator-ui/package.json
COPY web/package.json ./web/package.json
COPY services/data/web/package.json ./services/data/web/package.json
COPY services/knowledge-next/ui/package.json ./services/knowledge-next/ui/package.json
COPY --from=platform-deps npm/cache /npm-cache
RUN --network=none npm ci --offline --ignore-scripts --cache=/npm-cache
COPY packages/ui-boundary/ ./packages/ui-boundary/
COPY packages/operator-ui/ ./packages/operator-ui/
COPY web/ ./web/
COPY services/data/web/ ./services/data/web/
COPY services/knowledge-next/ui/ ./services/knowledge-next/ui/
WORKDIR /workspace/web
RUN --network=none npm run build
WORKDIR /workspace/services/data/web
RUN --network=none npm run build
WORKDIR /workspace/services/knowledge-next/ui
RUN --network=none npm run build

FROM platform-base-nginx
COPY --from=web /workspace/.build/web/ /usr/share/nginx/html/
COPY --from=web /workspace/.build/data/ /usr/share/nginx/html/data/
COPY --from=web /workspace/.build/knowledge/ /usr/share/nginx/html/knowledge/
