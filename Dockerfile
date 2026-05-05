FROM mcr.microsoft.com/playwright/python:v1.46.0-jammy

WORKDIR /app

# Install Python deps (keyring excluded — not available without a desktop session)
COPY backend/requirements.txt requirements.txt
RUN pip install --no-cache-dir \
      fastapi==0.115.0 \
      "uvicorn[standard]==0.30.6" \
      httpx==0.27.2 \
      playwright==1.46.0 \
      pydantic==2.8.2 \
      sqlalchemy==2.0.35 \
      python-multipart==0.0.9 && \
    playwright install chromium

# Copy application code
COPY backend/ ./backend/
COPY public/  ./public/

# Store history DB in /tmp (writable in any container runtime)
ENV SUPERMARKET_STORAGE_DIR=/tmp/supermarket-compare

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
