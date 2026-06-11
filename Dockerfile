FROM python:3.12-slim
RUN apt-get update && apt-get install -y ffmpeg fonts-open-sans curl unzip chromium nodejs npm && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /usr/local/share/fonts/leanlead && curl -fsSL "https://github.com/rsms/inter/releases/download/v4.0/Inter-4.0.zip" -o /tmp/inter.zip && unzip -q /tmp/inter.zip -d /tmp/inter && cp /tmp/inter/extras/otf/Inter-Bold.otf /usr/local/share/fonts/leanlead/ && rm -rf /tmp/inter /tmp/inter.zip && curl -fsSL "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Bold.ttf" -o /usr/local/share/fonts/leanlead/Montserrat-Bold.ttf || true && curl -fsSL "https://github.com/googlefonts/poppins/raw/main/fonts/Poppins-Bold.ttf" -o /usr/local/share/fonts/leanlead/Poppins-Bold.ttf || true && curl -fsSL "https://github.com/dharmatype/Bebas-Neue/raw/master/fonts/BebasNeue(2018)byDaFontMaker/Ttf/BebasNeue-Regular.ttf" -o /usr/local/share/fonts/leanlead/BebasNeue-Regular.ttf || true && curl -fsSL "https://github.com/googlefonts/AntonFont/raw/main/fonts/ttf/Anton-Regular.ttf" -o /usr/local/share/fonts/leanlead/Anton-Regular.ttf || true && curl -fsSL "https://github.com/googlefonts/dm-fonts/raw/main/Sans/fonts/ttf/DMSans-Bold.ttf" -o /usr/local/share/fonts/leanlead/DMSans-Bold.ttf || true && curl -fsSL "https://github.com/googlefonts/PlayfairDisplay/raw/main/fonts/ttf/PlayfairDisplay-Bold.ttf" -o /usr/local/share/fonts/leanlead/PlayfairDisplay-Bold.ttf || true && fc-cache -f -v
RUN npm install -g hyperframes 2>/dev/null || true
COPY fonts/ /usr/local/share/fonts/leanlead/
RUN fc-cache -f -v 2>/dev/null || true
WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY editor_frontend/ /app/editor_frontend/
COPY frontend/ /app/frontend/
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]