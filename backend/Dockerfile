# -----------------------------------------------------------------------------
# ReDOCX production image
# Conversion-runtime additions in this revision:
#   - pinned, signature-verified veraPDF CLI for fail-closed PDF/A-2b validation
#   - explicit pdf2docx runtime verification for editable PDF -> DOCX conversion
# -----------------------------------------------------------------------------

FROM eclipse-temurin:17-jdk-jammy AS verapdf-builder

ARG VERAPDF_SERIES=1.30
ARG VERAPDF_VERSION=1.30.2
ARG VERAPDF_SIGNING_FINGERPRINT=13DD102B4DD69354D12DE5A83184863278B17FE7

WORKDIR /tmp/verapdf-build

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        unzip; \
    rm -rf /var/lib/apt/lists/*

# Download a pinned stable veraPDF release and verify the detached signature.
# The embedded fingerprint is the fingerprint published by the veraPDF project.
RUN set -eux; \
    installer="verapdf-greenfield-${VERAPDF_VERSION}-installer.zip"; \
    base_url="https://software.verapdf.org/releases/${VERAPDF_SERIES}"; \
    curl --fail --show-error --silent --location \
        "${base_url}/${installer}" \
        --output "/tmp/verapdf-build/${installer}"; \
    curl --fail --show-error --silent --location \
        "${base_url}/${installer}.asc" \
        --output "/tmp/verapdf-build/${installer}.asc"; \
    curl --fail --show-error --silent --location \
        "https://software.verapdf.org/keys/KEY" \
        --output /tmp/verapdf-build/verapdf-signing-key.asc; \
    actual_fingerprint="$(gpg --batch --with-colons --import-options show-only --import \
        /tmp/verapdf-build/verapdf-signing-key.asc \
        | awk -F: '$1 == "fpr" { print $10; exit }')"; \
    test "${actual_fingerprint}" = "${VERAPDF_SIGNING_FINGERPRINT}"; \
    gpg --batch --import /tmp/verapdf-build/verapdf-signing-key.asc; \
    gpg --batch --verify \
        "/tmp/verapdf-build/${installer}.asc" \
        "/tmp/verapdf-build/${installer}"; \
    unzip -q "/tmp/verapdf-build/${installer}" -d /tmp/verapdf-build/unpacked

# veraPDF 1.30+ provides a dedicated CLI pack. Install only the CLI to minimize
# image size and avoid shipping the desktop GUI/documentation in the API runtime.
RUN set -eux; \
    printf '%s\n' \
      '<?xml version="1.0" encoding="UTF-8" standalone="no"?>' \
      '<AutomatedInstallation langpack="eng">' \
      '  <com.izforge.izpack.panels.htmlhello.HTMLHelloPanel id="welcome"/>' \
      '  <com.izforge.izpack.panels.target.TargetPanel id="install_dir">' \
      '    <installpath>/opt/verapdf</installpath>' \
      '  </com.izforge.izpack.panels.target.TargetPanel>' \
      '  <com.izforge.izpack.panels.packs.PacksPanel id="sdk_pack_select">' \
      '    <pack index="0" name="veraPDF GUI" selected="false"/>' \
      '    <pack index="1" name="veraPDF CLI" selected="true"/>' \
      '    <pack index="2" name="veraPDF Documentation" selected="false"/>' \
      '    <pack index="3" name="veraPDF Sample Plugins" selected="false"/>' \
      '  </com.izforge.izpack.panels.packs.PacksPanel>' \
      '  <com.izforge.izpack.panels.install.InstallPanel id="install"/>' \
      '  <com.izforge.izpack.panels.finish.FinishPanel id="finish"/>' \
      '</AutomatedInstallation>' \
      > /tmp/verapdf-build/auto-install.xml; \
    installer_jar="$(find /tmp/verapdf-build/unpacked -type f \
        -name "verapdf-izpack-installer-${VERAPDF_VERSION}.jar" \
        -print -quit)"; \
    test -n "${installer_jar}"; \
    java -jar "${installer_jar}" /tmp/verapdf-build/auto-install.xml; \
    test -x /opt/verapdf/verapdf; \
    /opt/verapdf/verapdf --version | grep -F "${VERAPDF_VERSION}"

# Build the same minimal Java runtime module set used by veraPDF's official
# container image. Ubuntu/Jammy and Debian/Bookworm are both glibc based.
RUN set -eux; \
    "$JAVA_HOME/bin/jlink" \
        --add-modules java.base,java.compiler,java.logging,java.xml,jdk.crypto.ec,java.desktop,jdk.management \
        --strip-debug \
        --no-man-pages \
        --no-header-files \
        --compress=2 \
        --output /opt/verapdf-jre; \
    /opt/verapdf-jre/bin/java -version


FROM python:3.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV SOFFICE_PATH=/usr/bin/soffice
ENV REDOCX_LIBREOFFICE_UNO_PYTHON=/usr/bin/python3

# Conversion runtime configuration. PDF/A validation is deliberately fail-closed:
# a generated PDF is not returned as PDF/A unless veraPDF independently validates it.
ENV JAVA_HOME=/opt/java/openjdk
ENV PATH=/opt/java/openjdk/bin:/opt/verapdf:/usr/local/bin:$PATH
ENV VERAPDF_PATH=/opt/verapdf/verapdf
ENV REDOCX_PDFA_VALIDATOR_MODE=required
ENV GHOSTSCRIPT_PATH=/usr/bin/gs
ENV REDOCX_PDFA_ICC_PROFILE=/usr/share/color/icc/ghostscript/srgb.icc
ENV REDOCX_PDF_TO_DOCX_MODE=auto

ARG CLAMAV_VERSION=1.4.5

WORKDIR /app/backend

COPY --from=verapdf-builder /opt/verapdf-jre /opt/java/openjdk
COPY --from=verapdf-builder /opt/verapdf /opt/verapdf

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
    python3-uno \
    fontconfig \
    libharfbuzz0b \
    fonts-dejavu \
    fonts-liberation \
    fonts-crosextra-carlito \
    fonts-crosextra-caladea \
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

# Fail the image build if any runtime required by the supported conversion matrix
# is absent. This includes both the editable PDF->DOCX engine and independent
# PDF/A validation; neither may silently disappear from a production image.
RUN set -eux; \
    command -v ffmpeg; \
    command -v ffprobe; \
    ffmpeg -hide_banner -encoders 2>/dev/null | grep -q "libx264"; \
    ffmpeg -hide_banner -encoders 2>/dev/null | grep -q "libmp3lame"; \
    ffmpeg -hide_banner -encoders 2>/dev/null | grep -q "libopus"; \
    ffmpeg -hide_banner -encoders 2>/dev/null | grep -Eq "[[:space:]]aac[[:space:]]"; \
    ffmpeg -hide_banner -encoders 2>/dev/null | grep -Eq "[[:space:]]flac[[:space:]]"; \
    command -v soffice; \
    soffice --version; \
    command -v gs; \
    gs --version; \
    test -x /usr/bin/python3; \
    /usr/bin/python3 -c "import uno; print('libreoffice-uno-ok')"; \
    fc-match Calibri | grep -qi Carlito; \
    fc-match Cambria | grep -qi Caladea; \
    test -s /usr/share/color/icc/ghostscript/srgb.icc; \
    test -n "$(find /usr/share/ghostscript -path '*/lib/PDFA_def.ps' -type f -print -quit)"; \
    test -x "${VERAPDF_PATH}"; \
    "${VERAPDF_PATH}" --version | grep -F "veraPDF"; \
    java -version; \
    python -c "import fitz, openpyxl, pptx, weasyprint; from PIL import Image; from pdf2docx import Converter; print('conversion-runtime-ok')"

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
