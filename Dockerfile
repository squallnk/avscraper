# 多阶段构建：前端产物打进后端镜像，只出一个端口
FROM node:22-alpine AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    AVS_DATA_DIR=/app/data \
    AVS_HOST=0.0.0.0 \
    AVS_PORT=9300
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server/ ./server/
COPY --from=web /app/web/dist ./web/dist
RUN mkdir -p /app/data
EXPOSE 9300
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9300/api/health', timeout=3).status==200 else 1)"
CMD ["python", "-m", "server.main"]
