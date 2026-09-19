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

# 构建标记必须放在依赖层**之后**：ARG/ENV 一变就会让它后面的层全部失效，
# 放前面等于每次提交都重装一遍依赖。
# 之前 workflow 传了 BUILD_SHA / BUILD_TIME 但这里没有 ARG 接住，
# 参数被静默丢掉，/api/health 一直显示 build_sha=dev ——
# 恰恰是排查"容器里跑的是哪个版本"时最需要它。
ARG BUILD_SHA=dev
ARG BUILD_TIME=
ENV AVS_BUILD_SHA=${BUILD_SHA} \
    AVS_BUILD_TIME=${BUILD_TIME}

COPY server/ ./server/
COPY --from=web /app/web/dist ./web/dist
RUN mkdir -p /app/data
EXPOSE 9300
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9300/api/health', timeout=3).status==200 else 1)"
CMD ["python", "-m", "server.main"]
