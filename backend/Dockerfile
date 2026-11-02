FROM python:3.12-slim

# onnxruntime/ultralytics runtime libs (slim lacks them).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .
COPY src/ ./src/

ENV HOST=0.0.0.0 PORT=8000
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "komaverse.main:app", "--host", "0.0.0.0", "--port", "8000"]
