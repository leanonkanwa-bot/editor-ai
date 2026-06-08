FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    fonts-open-sans \
    curl \
    unzip \
    chromium \
    && rm -rf /var/lib/apt/lists/*

# Install fonts via Python urllib — follows redirects reliably on Railway,
# unlike curl which hits GitHub raw 301s inconsistently in Docker builds.
# Inter Bold still uses the zip release (confirmed working); all others use
# Python so each download prints its own OK/FAIL line in the build log.
RUN mkdir -p /usr/local/share/fonts/leanlead && \
    # Inter Bold — rsms/inter GitHub releases zip (confirmed working with curl)
    curl -fsSL "https://github.com/rsms/inter/releases/download/v4.0/Inter-4.0.zip" \
         -o /tmp/inter.zip && \
    unzip -q /tmp/inter.zip -d /tmp/inter && \
    cp /tmp/inter/extras/otf/Inter-Bold.otf /usr/local/share/fonts/leanlead/ && \
    rm -rf /tmp/inter /tmp/inter.zip && \
    # All other fonts — Python urllib follows redirects, prints per-font status
    python3 -c "
import urllib.request, os, sys
fonts = {
    'Montserrat-Bold.ttf':      'https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Bold.ttf',
    'BebasNeue-Regular.ttf':    'https://github.com/dharmatype/Bebas-Neue/raw/master/fonts/BebasNeue(2018)byDaFontMaker/Ttf/BebasNeue-Regular.ttf',
    'Anton-Regular.ttf':        'https://github.com/googlefonts/AntonFont/raw/main/fonts/ttf/Anton-Regular.ttf',
    'DMSans-Bold.ttf':          'https://github.com/googlefonts/dm-fonts/raw/main/Sans/fonts/ttf/DMSans-Bold.ttf',
    'Poppins-Bold.ttf':         'https://github.com/googlefonts/poppins/raw/main/fonts/Poppins-Bold.ttf',
    'PlayfairDisplay-Bold.ttf': 'https://github.com/googlefonts/PlayfairDisplay/raw/main/fonts/ttf/PlayfairDisplay-Bold.ttf',
}
dest = '/usr/local/share/fonts/leanlead'
ok = 0
for name, url in fonts.items():
    path = os.path.join(dest, name)
    try:
        urllib.request.urlretrieve(url, path)
        size = os.path.getsize(path)
        if size < 1000:
            raise ValueError(f'suspiciously small: {size} bytes')
        print(f'OK  {name} ({size} bytes)')
        ok += 1
    except Exception as e:
        print(f'FAIL {name}: {e}', file=sys.stderr)
print(f'{ok}/{len(fonts)} fonts downloaded')
" && \
    fc-cache -f -v && \
    echo "=== INSTALLED LEANLEAD FONTS ===" && \
    fc-list | grep -i "leanlead" | sort && \
    echo "=== COUNT ===" && \
    fc-list | grep -i "leanlead" | wc -l

# Custom fonts (Quicksand, SF Compact Bold, etc.) — drop TTF/OTF files into fonts/ before building.
COPY fonts/ /usr/local/share/fonts/leanlead/
RUN fc-cache -f -v 2>/dev/null || true

RUN apt-get update && apt-get install -y \
    nodejs npm \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g hyperframes 2>/dev/null || true

WORKDIR /app/backend

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY editor_frontend/ /app/editor_frontend/
COPY frontend/ /app/frontend/

EXPOSE 8000

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
