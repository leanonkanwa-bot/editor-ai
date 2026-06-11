"""
Font Manager — downloads Google Fonts on demand and caches them.
Fonts are cached in /tmp/leanlead_fonts/ between renders on the same container.
Pre-installed fonts in /usr/local/share/fonts/leanlead/ are used first.
"""

from __future__ import annotations

import re
import subprocess
import urllib.request
from pathlib import Path

FONT_CACHE_DIR  = Path("/tmp/leanlead_fonts")
PREINSTALLED_DIR = Path("/usr/local/share/fonts/leanlead")
SYSTEM_FALLBACK  = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Pre-installed fonts — available without download
PREINSTALLED: dict[str, Path] = {
    "inter bold":            PREINSTALLED_DIR / "Inter-Bold.otf",
    "inter":                 PREINSTALLED_DIR / "Inter-Bold.otf",
    "montserrat bold":       PREINSTALLED_DIR / "Montserrat-Bold.ttf",
    "montserrat":            PREINSTALLED_DIR / "Montserrat-Bold.ttf",
    "poppins bold":          PREINSTALLED_DIR / "Poppins-Bold.ttf",
    "poppins":               PREINSTALLED_DIR / "Poppins-Bold.ttf",
    "bebas neue":            PREINSTALLED_DIR / "BebasNeue-Regular.ttf",
    "bebas":                 PREINSTALLED_DIR / "BebasNeue-Regular.ttf",
    "anton":                 PREINSTALLED_DIR / "Anton-Regular.ttf",
    "dm sans bold":          PREINSTALLED_DIR / "DMSans-Bold.ttf",
    "dm sans":               PREINSTALLED_DIR / "DMSans-Bold.ttf",
    "playfair display bold": PREINSTALLED_DIR / "PlayfairDisplay-Bold.ttf",
    "playfair display":      PREINSTALLED_DIR / "PlayfairDisplay-Bold.ttf",
    "quicksand bold":        PREINSTALLED_DIR / "Quicksand-Bold.ttf",
    "quicksand":             PREINSTALLED_DIR / "Quicksand-Bold.ttf",
    "open sans":             Path("/usr/share/fonts/truetype/open-sans/OpenSans-Bold.ttf"),
    "dejavu sans bold":      Path(SYSTEM_FALLBACK),
    "dejavu sans":           Path(SYSTEM_FALLBACK),
}

# Fonts that ship as "Regular" despite being display/decorative faces
_DISPLAY_WEIGHTS = {"bebas neue", "anton", "bebas"}

_STYLE_FONTS: dict[str, list[tuple[str, int]]] = {
    "priestley": [("Inter", 700), ("Inter", 900)],
    "hormozi":   [("Inter", 800), ("Montserrat", 700)],
    "cinematic": [("Playfair Display", 700), ("Montserrat", 400)],
    "viral":     [("Inter", 700), ("Montserrat", 700)],
}


def _normalize(name: str) -> str:
    return name.lower().strip()


def _cache_path(font_name: str, weight: int = 700) -> Path:
    FONT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-z0-9]", "_", _normalize(font_name))
    return FONT_CACHE_DIR / f"{safe}_{weight}.ttf"


def _download_from_google(font_name: str, weight: int = 700) -> Path | None:
    """Download a font file from the Google Fonts CSS2 API and cache it."""
    try:
        family = font_name.replace(" ", "+")
        url = (
            f"https://fonts.googleapis.com/css2?family={family}:wght@{weight}&display=swap"
        )
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            css = resp.read().decode("utf-8")

        font_urls = re.findall(r"url\(([^)]+)\)", css)
        if not font_urls:
            print(f"[FONT] No URLs in Google Fonts CSS for: {font_name}")
            return None

        dst = _cache_path(font_name, weight)
        for font_url in font_urls:
            font_url = font_url.strip("'\"")
            try:
                req2 = urllib.request.Request(
                    font_url,
                    headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
                )
                with urllib.request.urlopen(req2, timeout=15) as resp2:
                    data = resp2.read()
                if len(data) < 1000:
                    continue
                dst.write_bytes(data)
                subprocess.run(
                    ["fc-cache", "-f", str(FONT_CACHE_DIR)],
                    capture_output=True, timeout=10,
                )
                print(
                    f"[FONT] Downloaded: {font_name} weight={weight} "
                    f"→ {dst} ({len(data)} bytes)"
                )
                return dst
            except Exception as inner:
                print(f"[FONT] URL fetch failed ({font_url[:60]}…): {inner}")
                continue

    except Exception as exc:
        print(f"[FONT] Download failed for '{font_name}' weight={weight}: {exc}")
    return None


def get_font_path(font_name: str, weight: int = 700) -> str:
    """Return an absolute font file path usable as FFmpeg fontfile= value.

    Priority: pre-installed → /tmp cache → Google Fonts download → DejaVu fallback.
    """
    key = _normalize(font_name)

    # 1. Pre-installed
    if key in PREINSTALLED:
        path = PREINSTALLED[key]
        if path.exists():
            print(f"[FONT] Pre-installed: {font_name} → {path}")
            return str(path)

    # 2. Cached from a previous download this session
    cached = _cache_path(font_name, weight)
    if cached.exists() and cached.stat().st_size > 1000:
        print(f"[FONT] Cached: {font_name} → {cached}")
        return str(cached)

    # 3. Download from Google Fonts
    print(f"[FONT] Downloading from Google Fonts: {font_name} weight={weight}")
    downloaded = _download_from_google(font_name, weight)
    if downloaded and downloaded.exists():
        return str(downloaded)

    # 4. System fallback
    print(f"[FONT] FALLBACK to DejaVu Sans for: {font_name}")
    return SYSTEM_FALLBACK


def get_font_family(font_name: str) -> str:
    """Return the ASS Fontname family string (weight suffixes stripped)."""
    family = re.sub(
        r"\s*(Bold|Regular|Light|Medium|SemiBold|ExtraBold|Black|Italic)\s*$",
        "",
        font_name,
        flags=re.IGNORECASE,
    ).strip()
    return family if family else "DejaVu Sans"


def preload_style_fonts(editing_style: str) -> None:
    """Pre-download all fonts required for the given editing style."""
    fonts = _STYLE_FONTS.get(editing_style, [("Inter", 700)])
    print(f"[FONT] Preloading {len(fonts)} font(s) for style '{editing_style}'")
    for font_name, weight in fonts:
        get_font_path(font_name, weight)
    print(f"[FONT] Preload complete for style '{editing_style}'")
