FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    fonts-open-sans \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Inter Bold — try apt package first, fall back to GitHub release download.
RUN apt-get update && ( apt-get install -y fonts-inter 2>/dev/null || ( \
    curl -fsSL https://github.com/rsms/inter/releases/download/v4.0/Inter-4.0.zip \
         -o /tmp/inter.zip && \
    unzip -q /tmp/inter.zip -d /tmp/inter && \
    mkdir -p /usr/local/share/fonts/leanlead && \
    cp /tmp/inter/extras/otf/Inter-Bold.otf /usr/local/share/fonts/leanlead/ && \
    rm -rf /tmp/inter /tmp/inter.zip ) ) && rm -rf /var/lib/apt/lists/*

# Google Fonts: Montserrat, DM Sans, Bebas Neue, Anton — direct TTF from GitHub raw
RUN mkdir -p /usr/local/share/fonts/leanlead && \
    curl -fsSL "https://github.com/google/fonts/raw/main/ofl/montserrat/static/Montserrat-Bold.ttf" \
         -o /usr/local/share/fonts/leanlead/Montserrat-Bold.ttf && \
    curl -fsSL "https://github.com/google/fonts/raw/main/ofl/bebasneue/BebasNeue-Regular.ttf" \
         -o /usr/local/share/fonts/leanlead/BebasNeue-Regular.ttf && \
    curl -fsSL "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf" \
         -o /usr/local/share/fonts/leanlead/Anton-Regular.ttf && \
    curl -fsSL "https://github.com/google/fonts/raw/main/ofl/dmsans/static/DMSans-Bold.ttf" \
         -o /usr/local/share/fonts/leanlead/DMSans-Bold.ttf && \
    fc-cache -f -v

# Custom fonts (Quicksand, SF Compact Bold, etc.) — drop TTF/OTF files into fonts/ before building.
COPY fonts/ /usr/local/share/fonts/leanlead/
RUN fc-cache -f -v 2>/dev/null || true

WORKDIR /app/backend

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY editor_frontend/ /app/editor_frontend/
COPY frontend/ /app/frontend/

EXPOSE 8000

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
