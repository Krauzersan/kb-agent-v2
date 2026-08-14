FROM python:3.11-slim

# Tesseract (RU+EN) for OCR on scanned documents/images, libgomp1 for torch/sentence-transformers
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY app/requirements.txt .
# CPU build of torch (matches deploy/install.sh) — much smaller than the default CUDA wheel
RUN pip install --no-cache-dir torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

COPY app/ .

ENV HOST=0.0.0.0 \
    PORT=8746 \
    DATA_DIR=/data

RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8746

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import urllib.request as u; u.urlopen('http://localhost:${PORT}/health', timeout=3)" || exit 1

CMD ["sh", "-c", "uvicorn main:app --host ${HOST} --port ${PORT}"]
