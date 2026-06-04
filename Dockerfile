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

# Google Fonts: Montserrat, DM Sans, Bebas Neue, Anton
RUN mkdir -p /usr/local/share/fonts/leanlead && cd /tmp && \
    curl -fsSL "https://fonts.google.com/download?family=Montserrat" -o montserrat.zip && \
    unzip -q montserrat.zip && \
    cp Montserrat/static/Montserrat-Bold.ttf /usr/local/share/fonts/leanlead/ && \
    rm -rf Montserrat montserrat.zip && \
    curl -fsSL "https://fonts.google.com/download?family=DM+Sans" -o dmsans.zip && \
    unzip -q dmsans.zip && \
    ( cp "DM_Sans/static/DMSans-Bold.ttf" /usr/local/share/fonts/leanlead/ 2>/dev/null || \
      cp "DM_Sans/static/DMSans_18pt-Bold.ttf" /usr/local/share/fonts/leanlead/DMSans-Bold.ttf 2>/dev/null || true ) && \
    rm -rf DM_Sans dmsans.zip && \
    curl -fsSL "https://fonts.google.com/download?family=Bebas+Neue" -o bebas.zip && \
    unzip -q bebas.zip && \
    cp "Bebas_Neue/BebasNeue-Regular.ttf" /usr/local/share/fonts/leanlead/ && \
    rm -rf Bebas_Neue bebas.zip && \
    curl -fsSL "https://fonts.google.com/download?family=Anton" -o anton.zip && \
    unzip -q anton.zip && \
    cp "Anton/Anton-Regular.ttf" /usr/local/share/fonts/leanlead/ && \
    rm -rf Anton anton.zip

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
