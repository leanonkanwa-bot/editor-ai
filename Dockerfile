FROM python:3.12-slim
RUN apt-get update && apt-get install -y ffmpeg fonts-open-sans curl unzip chromium nodejs npm \
    xvfb xauth x11-utils \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g puppeteer --unsafe-perm 2>/dev/null || true
RUN npx puppeteer browsers install chrome 2>/dev/null || true
RUN npx @puppeteer/browsers install chrome-headless-shell@stable \
    --path /usr/local/lib/chrome && \
    test -n "$(find /usr/local/lib/chrome -name 'chrome-headless-shell' -type f | head -1)" \
    || (echo "ERROR: chrome-headless-shell binary not found after install" && exit 1)
RUN mkdir -p /usr/local/share/fonts/leanlead && curl -fsSL "https://github.com/rsms/inter/releases/download/v4.0/Inter-4.0.zip" -o /tmp/inter.zip && unzip -q /tmp/inter.zip -d /tmp/inter && cp /tmp/inter/extras/otf/Inter-Bold.otf /usr/local/share/fonts/leanlead/ && rm -rf /tmp/inter /tmp/inter.zip && curl -fsSL "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Bold.ttf" -o /usr/local/share/fonts/leanlead/Montserrat-Bold.ttf || true && curl -fsSL "https://github.com/googlefonts/poppins/raw/main/fonts/Poppins-Bold.ttf" -o /usr/local/share/fonts/leanlead/Poppins-Bold.ttf || true && curl -fsSL "https://github.com/dharmatype/Bebas-Neue/raw/master/fonts/BebasNeue(2018)byDaFontMaker/Ttf/BebasNeue-Regular.ttf" -o /usr/local/share/fonts/leanlead/BebasNeue-Regular.ttf || true && curl -fsSL "https://github.com/googlefonts/AntonFont/raw/main/fonts/ttf/Anton-Regular.ttf" -o /usr/local/share/fonts/leanlead/Anton-Regular.ttf || true && curl -fsSL "https://github.com/googlefonts/dm-fonts/raw/main/Sans/fonts/ttf/DMSans-Bold.ttf" -o /usr/local/share/fonts/leanlead/DMSans-Bold.ttf || true && curl -fsSL "https://github.com/googlefonts/PlayfairDisplay/raw/main/fonts/ttf/PlayfairDisplay-Bold.ttf" -o /usr/local/share/fonts/leanlead/PlayfairDisplay-Bold.ttf || true && fc-cache -f -v
COPY fonts/ /usr/local/share/fonts/leanlead/
RUN fc-cache -f -v 2>/dev/null || true
WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
ARG RAILWAY_GIT_COMMIT_SHA=""
RUN if [ -n "$RAILWAY_GIT_COMMIT_SHA" ]; then echo "$RAILWAY_GIT_COMMIT_SHA" > COMMIT_HASH; fi
RUN cd app/engine && npm ci --omit=dev 2>/dev/null || npm install --omit=dev 2>/dev/null || true
RUN npx hyperframes browser ensure 2>/dev/null || true
COPY editor_frontend/ /app/editor_frontend/
COPY frontend/ /app/frontend/
EXPOSE 8000
CMD ["sh", "-c", "Xvfb :99 -screen 0 1920x1080x24 & sleep 1 && DISPLAY=:99 uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]