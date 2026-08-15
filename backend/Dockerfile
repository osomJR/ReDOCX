FROM python:3.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV SOFFICE_PATH=/usr/bin/soffice

ARG CLAMAV_VERSION=1.4.5

WORKDIR /app/backend

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    ffmpeg \
    ghostscript \
    tesseract-ocr \
    tesseract-ocr-osd \
    tesseract-ocr-eng \
    tesseract-ocr-ara \
    tesseract-ocr-deu \
    tesseract-ocr-spa \
    tesseract-ocr-fas \
    tesseract-ocr-fra \
    tesseract-ocr-hin \
    tesseract-ocr-ita \
    tesseract-ocr-jpn \
    tesseract-ocr-kor \
    tesseract-ocr-nld \
    tesseract-ocr-pol \
    tesseract-ocr-por \
    tesseract-ocr-rus \
    tesseract-ocr-swa \
    tesseract-ocr-tur \
    tesseract-ocr-ukr \
    tesseract-ocr-yor \
    tesseract-ocr-chi-sim \
    tesseract-ocr-chi-tra \
    libgl1 \
    libglib2.0-0 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz-subset0 \
    libpq5 \
    libreoffice \
    libreoffice-writer \
    libreoffice-calc \
    libreoffice-impress \
    fontconfig \
    libharfbuzz0b \
    fonts-dejavu \
    fonts-liberation \
    && curl -fsSL -o /tmp/clamav.deb \
       "https://www.clamav.net/downloads/production/clamav-${CLAMAV_VERSION}.linux.x86_64.deb" \
    && apt-get install -y /tmp/clamav.deb \
    && clamscan --version \
    && command -v clamdscan \
    && command -v clamd \
    && rm -f /tmp/clamav.deb \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    if ! getent group clamav >/dev/null; then groupadd --system clamav; fi; \
    if ! id -u clamav >/dev/null 2>&1; then \
        useradd --system \
            --gid clamav \
            --home-dir /var/lib/clamav \
            --shell /usr/sbin/nologin \
            clamav; \
    fi; \
    mkdir -p /var/lib/clamav /var/run/clamav /var/log/clamav; \
    chown -R clamav:clamav /var/lib/clamav /var/run/clamav /var/log/clamav

COPY requirements.txt /app/backend/requirements.txt

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/backend/requirements.txt

# Fail the image build if any conversion runtime required by the supported
# production conversion matrix is absent or unusable.
RUN set -eux; \
    command -v soffice; \
    command -v gs; \
    test -s /usr/share/color/icc/ghostscript/srgb.icc; \
    test -n "$(find /usr/share/ghostscript -path '*/lib/PDFA_def.ps' -type f -print -quit)"; \
    python -c "import fitz, openpyxl, pptx, weasyprint; from PIL import Image; print('conversion-runtime-ok')"

COPY . /app/backend

RUN set -eux; \
    for font in \
        NotoSans-Regular.ttf \
        NotoSans-Bold.ttf \
        NotoNaskhArabic-Regular.ttf \
        NotoNaskhArabic-Bold.ttf \
        NotoSansDevanagari-Regular.ttf \
        NotoSansDevanagari-Bold.ttf \
        NotoSansSC-Regular.ttf \
        NotoSansTC-Regular.ttf \
        NotoSansJP-Regular.ttf \
        NotoSansKR-Regular.ttf \
        NotoSansMath-Regular.ttf \
        NotoSansSymbols2-Regular.ttf; \
    do \
        test -s "/app/backend/assets/fonts/${font}"; \
    done; \
    mkdir -p /usr/local/share/fonts; \
    ln -sfn /app/backend/assets/fonts /usr/local/share/fonts/redocx; \
    fc-cache -f

COPY robots.txt /app/backend/robots.txt

COPY start.sh /app/backend/start.sh
RUN chmod +x /app/backend/start.sh

EXPOSE 8080

CMD ["/app/backend/start.sh"]
