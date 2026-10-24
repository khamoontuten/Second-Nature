# Single image: builds the React workbench, then serves it and the API from FastAPI (uvicorn) on :8000.
# The repository layout is kept (/app/backend, /app/frontend/dist, /app/data/reference) because paths are
# resolved relative to the repository root.

FROM node:22-bookworm-slim AS web
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY backend/pyproject.toml backend/alembic.ini backend/
COPY backend/app backend/app
COPY backend/migrations backend/migrations
# editable install keeps the source tree in place, so repository-relative paths resolve inside the image
RUN pip install --no-cache-dir -e ./backend
COPY data/reference data/reference
COPY --from=web /src/frontend/dist frontend/dist
RUN useradd --create-home --uid 10001 sn && mkdir -p /data && chown sn /data
USER sn
ENV SN_DATA_DIR=/data
WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
