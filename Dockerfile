FROM node:24-bookworm-slim AS frontend
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
COPY public ./public
RUN npm run build

FROM node:24-bookworm-slim AS model-runtime
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-fund --no-audit

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATA_DIR=/app/data
WORKDIR /app
COPY requirements.lock ./
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 \
    gosu \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.lock \
    && useradd --create-home --uid 10001 lumina \
    && root_password="$(head -c 32 /dev/urandom | base64)" \
    && printf 'root:%s\n' "$root_password" | chpasswd \
    && unset root_password \
    && mkdir -p /app/data && chown lumina:lumina /app/data
COPY --chown=lumina:lumina server ./server
COPY --chown=lumina:lumina bridge ./bridge
COPY --from=model-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=model-runtime --chown=lumina:lumina /app/node_modules ./node_modules
COPY --from=frontend --chown=lumina:lumina /app/dist ./dist
COPY --chmod=755 docker-entrypoint.sh /app/docker-entrypoint.sh
USER root
ENTRYPOINT ["/app/docker-entrypoint.sh"]
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/api/health', timeout=3)"
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8090", "--workers", "1"]
