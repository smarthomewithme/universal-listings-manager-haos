FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN python -m pip install --no-cache-dir --only-binary=:all: -r requirements.txt
COPY app/ ./
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8099/health', timeout=4).close()"]
CMD ["python", "/app/server.py"]
