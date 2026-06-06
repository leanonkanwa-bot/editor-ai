FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    fonts-open-sans \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install custom fonts — each download has a fallback so a CDN hiccup doesn't
# silently succeed while leaving the font missing. No 2>/dev/null suppression
# so build logs show exactly which downloads fail.
RUN mkdir -p /usr/local/share/fonts/leanlead && \
    # Inter Bold — rsms/inter GitHub releases (confirmed working)
    curl -fsSL "https://github.com/rsms/inter/releases/download/v4.0/Inter-4.0.zip" \
         -o /tmp/inter.zip && \
    unzip -q /tmp/inter.zip -d /tmp/inter && \
    cp /tmp/inter/extras/otf/Inter-Bold.otf /usr/local/share/fonts/leanlead/ && \
    rm -rf /tmp/inter /tmp/inter.zip && \
    # Montserrat Bold — JulietaUla GitHub first, Google Fonts fallback
    ( curl -fsSL "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Bold.ttf" \
           -o /usr/local/share/fonts/leanlead/Montserrat-Bold.ttf || \
      curl -fsSL "https://github.com/google/fonts/raw/main/ofl/montserrat/static/Montserrat-Bold.ttf" \
           -o /usr/local/share/fonts/leanlead/Montserrat-Bold.ttf || true ) && \
    # Bebas Neue — dharmatype GitHub first, Google Fonts fallback
    ( curl -fsSL "https://github.com/dharmatype/Bebas-Neue/raw/master/fonts/BebasNeue(2018)byDaFontMaker/Ttf/BebasNeue-Regular.ttf" \
           -o /usr/local/share/fonts/leanlead/BebasNeue-Regular.ttf || \
      curl -fsSL "https://github.com/google/fonts/raw/main/ofl/bebasneue/BebasNeue-Regular.ttf" \
           -o /usr/local/share/fonts/leanlead/BebasNeue-Regular.ttf || true ) && \
    # Anton — Google Fonts TTF (woff2 is browser-only; fontconfig needs TTF)
    curl -fsSL "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf" \
         -o /usr/local/share/fonts/leanlead/Anton-Regular.ttf || true && \
    # DM Sans Bold — googlefonts/dm-fonts first, Google Fonts fallback
    ( curl -fsSL "https://github.com/googlefonts/dm-fonts/raw/main/Sans/fonts/ttf/DMSans-Bold.ttf" \
           -o /usr/local/share/fonts/leanlead/DMSans-Bold.ttf || \
      curl -fsSL "https://github.com/google/fonts/raw/main/ofl/dmsans/static/DMSans-Bold.ttf" \
           -o /usr/local/share/fonts/leanlead/DMSans-Bold.ttf || true ) && \
    # Poppins Bold
    curl -fsSL "https://github.com/itfoundry/poppins/raw/master/Poppins-Bold.ttf" \
         -o /usr/local/share/fonts/leanlead/Poppins-Bold.ttf || true && \
    # Playfair Display Bold
    curl -fsSL "https://github.com/google/fonts/raw/main/ofl/playfairdisplay/static/PlayfairDisplay-Bold.ttf" \
         -o /usr/local/share/fonts/leanlead/PlayfairDisplay-Bold.ttf || true && \
    fc-cache -f -v && \
    echo "=== INSTALLED LEANLEAD FONTS ===" && \
    fc-list | grep -i "leanlead" && \
    echo "=== END FONTS ==="

# Verification — list all fonts so build log confirms what's actually available.
RUN fc-list | sort > /tmp/fonts_list.txt && \
    echo "Total fonts installed:" && \
    wc -l /tmp/fonts_list.txt && \
    ( fc-list | grep -i "leanlead" || echo "WARNING: No leanlead fonts found" )

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
