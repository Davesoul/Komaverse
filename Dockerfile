# Komaverse all-in-one image: built web UI + API on one port.
# Keys ship empty: paste them in the studio Brain panel (Provider keys)
# or pass -e flags / env_file. The image wires KOMA_YOLO_URL to the
# public YOLOv12 comic-panel weights, so the first scan downloads them
# (~119MB) and detection works with no setup; without a reachable
# URL it falls back to heuristics.
#
#   docker build -t komaverse .
#   docker run -p 8000:8000 -v komaverse-data:/data komaverse
#   docker compose up --build   (same, plus env_file + named volume)

# ---- Stage 1: build the web UI ----
FROM node:22-slim AS web
WORKDIR /build/apps/studio-web
COPY apps/studio-web/package.json apps/studio-web/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY apps/studio-web/ ./
RUN npm run build

# ---- Stage 2: Python API + static UI ----
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    SERVE_WEB=true \
    PORT=8000 \
    DATABASE_URL=sqlite:////data/komaverse.db \
    MEDIA_DIR=/data/media \
    KOMA_YOLO_MODEL=/data/models/best-comic-panel-detection.pt \
    KOMA_YOLO_URL=https://huggingface.co/mosesb/best-comic-panel-detection/resolve/main/best.pt \
    KOMAVERSE_WEB_DIST=/srv/komaverse/apps/studio-web/dist \
    KOMAVERSE_SKILLS_DIR=/srv/komaverse/.agents/skills
# onnxruntime/ultralytics/ffmpeg runtime libs (slim lacks them).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 libglib2.0-0 libgl1 ffmpeg espeak-ng \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/komaverse
# CPU-only torch first so ultralytics does not pull CUDA wheels.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch
COPY backend/pyproject.toml backend/README.md ./backend/
RUN pip install --no-cache-dir ./backend
COPY backend/scripts ./backend/scripts
COPY .agents/skills ./.agents/skills
COPY --from=web /build/apps/studio-web/dist ./apps/studio-web/dist
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "komaverse.main:app", "--host", "0.0.0.0", "--port", "8000"]
