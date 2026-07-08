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
    libpq5 \
    libreoffice \
    libreoffice-writer \
    libreoffice-calc \
    libreoffice-impress \
    fonts-dejavu \
    fonts-liberation \
    && curl -fsSL -o /tmp/clamav.deb \
       "https://www.clamav.net/downloads/production/clamav-${CLAMAV_VERSION}.linux.x86_64.deb" \
    && apt-get install -y /tmp/clamav.deb \
    && clamscan --version \
    && clamdscan --version \
    && clamd --version \
    && rm -f /tmp/clamav.deb \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/backend/requirements.txt

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/backend/requirements.txt

COPY . /app/backend

COPY start.sh /app/backend/start.sh
RUN chmod +x /app/backend/start.sh

EXPOSE 8080

CMD ["/app/backend/start.sh"]