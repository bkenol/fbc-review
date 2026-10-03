# API only. The Angular client is a static bundle on Firebase Hosting, so no
# HTML is served from here and webapp/static is deliberately not copied.

# ── LibreDWG, built from the GNU release ─────────────────────────────────────
# dwg2dxf turns a DWG upload into the DXF that ezdxf reads (fbcreview/cad/
# convert.py). Decided by the owner on 2026-10-03 over the ODA File Converter
# and Autodesk Platform Services; docs/DEPLOYMENT.md §9a has the reasoning and
# what GPL-3.0 asks of anyone who gives this image to someone else.
#
# Built in a stage of its own because Debian has no libredwg package (none in
# any suite, checked 2026-10-03), and so the compiler never reaches the runtime
# image: only dwg2dxf and the one shared library it links against do. Same base
# image as the runtime stage, so the binary is linked against the glibc it will
# run on.
#
# The release tarball is checked against the SHA-256 recorded when the
# reference drawing (an AutoCAD 2018 DWG, 23 MB, eight layouts) was converted
# with it, and configured with the same feature flags as that build:
#   --disable-bindings --disable-python  no SWIG, no Python module; the service
#                                        runs the program, it never links it
#   --disable-docs                       no texinfo/TeX toolchain
# Only src/ (the library) and programs/ (dwg2dxf and its siblings) are built;
# the examples and the test suite are not. Measured outside Docker on 4 cores:
# about 6 minutes, nearly all of it the library, and the dwg2dxf it produces
# writes a DXF of the reference drawing byte-identical to the one it was
# measured with. Docker caches this stage, so it is paid once per version.
FROM python:3.12-slim AS libredwg

ARG LIBREDWG_VERSION=0.14
ARG LIBREDWG_SHA256=62ebb73b984f865960f20ed26619ea5f8789d5e3fd088fa40a2598384da81275

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN set -eu; \
    curl -fsSL -o libredwg.tar.xz \
        "https://ftp.gnu.org/gnu/libredwg/libredwg-${LIBREDWG_VERSION}.tar.xz"; \
    echo "${LIBREDWG_SHA256}  libredwg.tar.xz" | sha256sum -c -; \
    tar -xJf libredwg.tar.xz

WORKDIR /src/libredwg-${LIBREDWG_VERSION}
RUN set -eu; \
    ./configure --prefix=/usr/local \
        --disable-bindings --disable-python --disable-docs \
        --disable-dependency-tracking; \
    make -j"$(nproc)" -C src; \
    make -j"$(nproc)" -C programs; \
    make -C src install; \
    make -C programs install

# Stage exactly what the runtime image gets under /out, so it can be copied as
# one directory: a COPY of a symlink copies its target, and the library's two
# names (libredwg.so.0 -> libredwg.so.0.0.14) would become two full copies.
# The unversioned libredwg.so is a link-time name and is not needed to run.
# Stripped: 75 MB with debug information, 20 MB without.
# The licence and a note of where the source came from travel with the binary.
RUN set -eu; \
    mkdir -p /out/usr/local/bin /out/usr/local/lib /out/usr/local/share/doc/libredwg; \
    install -s /usr/local/bin/dwg2dxf /out/usr/local/bin/dwg2dxf; \
    cp -P /usr/local/lib/libredwg.so.0* /out/usr/local/lib/; \
    strip --strip-unneeded /out/usr/local/lib/libredwg.so.0.*; \
    cp COPYING /out/usr/local/share/doc/libredwg/COPYING; \
    printf '%s\n' \
        "GNU LibreDWG ${LIBREDWG_VERSION}, unmodified, licensed GPL-3.0-or-later (COPYING)." \
        "Corresponding source: https://ftp.gnu.org/gnu/libredwg/libredwg-${LIBREDWG_VERSION}.tar.xz" \
        "SHA-256: ${LIBREDWG_SHA256}" \
        "Built by this image's Dockerfile (stage 'libredwg'):" \
        "  ./configure --prefix=/usr/local --disable-bindings --disable-python --disable-docs" \
        "  make -C src && make -C programs && make -C src install && make -C programs install" \
        "Only dwg2dxf and libredwg.so.0 are installed, both stripped." \
        > /out/usr/local/share/doc/libredwg/SOURCE

# ── the service ──────────────────────────────────────────────────────────────
FROM python:3.12-slim

# tesseract-ocr        - OCR for rebuilding scanned sheets (webapp/convert.py)
# tesseract-ocr-eng    - the English language data; without it OCR raises
# libglib2.0-0, libgomp1 - OpenCV's runtime deps, even for the headless wheel
# Nothing here is needed by the deterministic vector path; a vector set never
# touches any of it.
#
# fonts-dejavu-core    - TrueType fonts for plotting a drawing (fbcreview/cad).
#                        ezdxf draws text as glyph outlines from the system's
#                        fonts; the slim image has none, and with none it falls
#                        back to a stand-in that draws every string as a box
#                        the size of the text. A drawing's own fonts (arial.ttf,
#                        an SHX like romans.shx) are not on a server, and ezdxf
#                        resolves each to DejaVuSans.ttf — measured on the
#                        reference drawing, whose 34 text styles name 13 font
#                        files and all 13 resolve to it — so this one package
#                        is what the measured rendering used. A PDF review
#                        never touches it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        libglib2.0-0 \
        libgomp1 \
        fonts-dejavu-core \
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

# The DWG converter and its library, from the stage above. /usr/local/lib is on
# Debian's loader path (ld.so.conf.d/libc.conf); ldconfig registers the new
# library, and running the program here fails the build, rather than the first
# DWG review, if it cannot load. FBC_DWG2DXF names it explicitly so the service
# never depends on PATH order (fbcreview/cad/convert.py, binary()).
COPY --from=libredwg /out/ /
RUN ldconfig && dwg2dxf --version
ENV FBC_DWG2DXF=/usr/local/bin/dwg2dxf

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

# ezdxf scans the system's font directories once and keeps the result in
# $XDG_CACHE_HOME/ezdxf/font_manager_cache.json. Built here, as the user the
# service runs as, so the first drawing review on a fresh instance does not pay
# for the scan; and stated rather than left to $HOME, so the cache built here
# is the one found at run time whatever the platform sets HOME to. The check
# fails the build if the fonts above were not found.
ENV XDG_CACHE_HOME=/home/fbc/.cache
RUN python -c "from ezdxf.fonts import fonts; \
assert fonts.font_manager.has_font('DejaVuSans.ttf'), 'ezdxf found no DejaVuSans.ttf'"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FBC_WORKERS=2

# No HEALTHCHECK: Cloud Run ignores Docker's and uses the startup probe
# configured on the service, which points at /healthz.
#
# Cloud Run injects PORT and it must be honoured; the default is for local runs.
CMD exec uvicorn webapp.server:app --host 0.0.0.0 --port ${PORT:-8080}
