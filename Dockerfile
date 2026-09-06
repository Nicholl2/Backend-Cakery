FROM python:3.12-slim

LABEL org.opencontainers.image.source=https://github.com/Nicholl2/Backend-Cakery

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd -m -u 1000 appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser . .

# app/main.py membuat static/products saat startup
RUN mkdir -p /app/static/products && chown -R appuser:appuser /app/static

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=5 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/openapi.json')"

# app dijalankan di belakang nginx, dan TLS diterminasi Cloudflare di edge.
# Tanpa --proxy-headers uvicorn mengabaikan X-Forwarded-Proto dan menyusun
# redirect root_path dengan skema http walau pengunjung datang lewat https,
# sehingga browser memblokirnya sebagai mixed content.
#
# --forwarded-allow-ips dibatasi ke rentang bridge Docker, bukan "*": yang
# menghubungi container ini selalu nginx dari dalam network compose.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='172.16.0.0/12'"]
