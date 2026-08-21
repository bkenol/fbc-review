FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY fbcreview/ ./fbcreview/
COPY webapp/ ./webapp/
COPY run.py .
ENV FBC_WORK_DIR=/data FBC_WORKERS=2 FBC_RETAIN_HOURS=24
RUN mkdir -p /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c \
  "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn","webapp.server:app","--host","0.0.0.0","--port","8000"]
