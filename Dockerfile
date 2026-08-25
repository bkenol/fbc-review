# API only. The Angular client is a static bundle on Firebase Hosting, so no
# HTML is served from here and webapp/static is deliberately not copied.
FROM python:3.12-slim

# tesseract-ocr        - OCR for rebuilding scanned sheets (webapp/convert.py)
# tesseract-ocr-eng    - the English language data; without it OCR raises
# libglib2.0-0, libgomp1 - OpenCV's runtime deps, even for the headless wheel
# Nothing here is needed by the deterministic vector path; a vector set never
# touches any of it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# PyMuPDF resolves Tesseract's data through TESSDATA_PREFIX. The path is
# version-stamped on Debian, so resolve it rather than hard-coding a guess that
# breaks on the next base-image bump.
RUN set -eu; \
    tessdata="$(dirname "$(find /usr/share -name 'eng.traineddata' -print -quit)")"; \
    test -n "$tessdata"; \
    printf '%s\n' "$tessdata" > /etc/tessdata_prefix; \
    echo "tessdata at $tessdata"
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fbcreview/ ./fbcreview/
COPY webapp/ ./webapp/
COPY run.py .

# The base version, so a container started without FBC_VERSION still reports
# something true rather than "unknown". CI sets FBC_VERSION and that wins; this
# is the floor under it. Last, because it changes on its own schedule and there
# is nothing below it to invalidate.
COPY VERSION .

# Run unprivileged. Added after pip install so the site-packages tree stays
# root-owned and read-only to the service.
RUN useradd --create-home --uid 10001 fbc \
    && mkdir -p /tmp/fbc \
    && chown -R fbc:fbc /tmp/fbc
USER fbc

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FBC_WORKERS=2

# No HEALTHCHECK: Cloud Run ignores Docker's and uses the startup probe
# configured on the service, which points at /healthz.
#
# Cloud Run injects PORT and it must be honoured; the default is for local runs.
CMD exec uvicorn webapp.server:app --host 0.0.0.0 --port ${PORT:-8080}
