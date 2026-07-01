"""Transactional email module — all sends are fire-and-forget, fail-open,
and skip founder profiles.

Gate: set EMAILS_ENABLED=true on Railway to activate. When unset or false,
every function is a no-op (safe for dev/test without side effects).
Sender: hello@leanretention.com (Resend verified domain).
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    import resend as _resend_lib
    _HAS_RESEND = True
except ImportError:
    _HAS_RESEND = False

_FROM = "hello@leanretention.com"
_APP_URL = "https://leanretention.com/app"
_PRICING_URL = "https://leanretention.com/#pricing"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _enabled() -> bool:
    return os.environ.get("EMAILS_ENABLED", "").strip().lower() == "true"


def _api_key() -> str:
    return os.environ.get("RESEND_API_KEY", "").strip()


def _send_blocking(to: str, subject: str, html: str) -> None:
    if not _HAS_RESEND or not _enabled():
        return
    key = _api_key()
    if not key:
        return
    try:
        _resend_lib.api_key = key
        _resend_lib.Emails.send({"from": _FROM, "to": [to], "subject": subject, "html": html})
        print(f"[email] sent '{subject}' to {to}")
    except Exception as e:
        print(f"[email] failed '{subject}' to {to}: {e}")


def _send(to: str, subject: str, html: str) -> None:
    """Dispatch in a daemon thread — never blocks the calling request."""
    threading.Thread(target=_send_blocking, args=(to, subject, html), daemon=True).start()


def _shell(body: str) -> str:
    """Wrap body in the standard dark-theme email shell."""
    return (
        '<!DOCTYPE html><html lang="fr">'
        '<head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        '<body style="margin:0;padding:0;background:#0d0d0d">'
        '<div style="font-family:\'Helvetica Neue\',Arial,sans-serif;background:#0d0d0d;'
        'color:#F5F5F6;padding:48px 32px;max-width:560px;margin:0 auto">'
        '<div style="margin-bottom:32px">'
        '<span style="background:#FF7751;color:#0d0d0d;font-size:12px;font-weight:800;'
        'padding:6px 14px;border-radius:6px;letter-spacing:.05em">LEANRETENTION</span>'
        '</div>'
        + body
        + '<div style="margin-top:48px;padding-top:20px;border-top:1px solid rgba(255,255,255,.08)">'
        '<p style="color:rgba(245,245,246,.3);font-size:11px;margin:0">'
        'LeanRetention · '
        '<a href="mailto:hello@leanretention.com" style="color:#FF7751;text-decoration:none">'
        'Nous contacter</a></p></div>'
        '</div></body></html>'
    )


def _cta(url: str, label: str) -> str:
    return (
        '<a href="' + url + '" style="display:inline-block;background:#FF7751;color:#fff;'
        'padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:700;'
        'font-size:15px;box-shadow:0 0 24px rgba(255,119,81,.35)">' + label + '</a>'
    )


def _h1(text: str) -> str:
    return (
        '<h1 style="font-size:26px;font-weight:800;letter-spacing:-.02em;margin:0 0 12px">'
        + text + '</h1>'
    )


def _p(text: str, extra_style: str = "") -> str:
    style = "font-size:16px;color:rgba(245,245,246,.65);line-height:1.7;margin:0 0 28px"
    if extra_style:
        style += ";" + extra_style
    return '<p style="' + style + '">' + text + '</p>'


def _first_name(profile: dict) -> str:
    parts = (profile.get("name") or profile.get("brandName") or "").split()
    return parts[0] if parts else "toi"


# ── A — Welcome ───────────────────────────────────────────────────────────────

def send_welcome(profile: dict) -> None:
    """Sent once when a new profile is created via Google OAuth (or waitlist)."""
    if profile.get("is_founder"):
        return
    email = (profile.get("email") or "").strip()
    if not email:
        return
    first = _first_name(profile)
    subject = "Bienvenue sur LeanRetention, " + first + " \U0001f44b"
    body = (
        _h1("Bienvenue sur LeanRetention, " + first + " \U0001f44b")
        + _p(
            "Ton compte est prêt. LeanRetention analyse ta vidéo, "
            "réécrit le hook, supprime les silences et exporte "
            "automatiquement en 9:16 et 16:9.<br><br>"
            "Ta première vidéo est "
            '<strong style="color:#FF7751">offerte</strong> — '
            "aucune carte requise."
        )
        + _cta(_APP_URL, "Commencer l’édition →")
    )
    _send(email, subject, _shell(body))


# ── B — Vidéo prête ───────────────────────────────────────────────────────────

def send_video_ready(profile: dict) -> None:
    """Sent when a render job reaches status=done."""
    if profile.get("is_founder"):
        return
    email = (profile.get("email") or "").strip()
    if not email:
        return
    first = _first_name(profile)
    subject = "Ta vidéo est prête à télécharger \U0001f3ac"
    body = (
        _h1("Ta vidéo est prête à télécharger \U0001f3ac")
        + _p(
            "Hey " + first + ", ton édition vient de se terminer. "
            "Télécharge-la directement depuis l’app."
        )
        + _cta(_APP_URL, "Voir ma vidéo →")
    )
    _send(email, subject, _shell(body))


# ── C — Re-engagement ─────────────────────────────────────────────────────────

def send_reengagement(profile: dict, profile_path: Path) -> bool:
    """Sent if no video edited in >=7 days. Caller checks edit recency first.
    Handles per-profile throttle (no re-send within another 7-day window).
    Returns True if an email was queued."""
    if profile.get("is_founder"):
        return False
    email = (profile.get("email") or "").strip()
    if not email:
        return False
    last_ts = profile.get("reengagement_sent_at")
    if last_ts and (time.time() - float(last_ts)) < 7 * 24 * 3600:
        return False
    first = _first_name(profile)
    subject = "Tu n’as pas édité de vidéo depuis une semaine..."
    body = (
        _h1("Tu n’as pas édité de vidéo depuis une semaine...")
        + _p(
            "Hey " + first + ", ça fait 7 jours que tu n’es plus passé par ici.<br><br>"
            "Chaque vidéo non éditée, c’est du temps perdu et de la "
            "rétention qui s’envole. Reprends là où tu t’es "
            "arrêté — moins de 5 minutes pour uploader et lancer l’édition."
        )
        + _cta(_APP_URL, "Reprendre l’édition →")
    )
    try:
        profile["reengagement_sent_at"] = time.time()
        profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[email] throttle stamp failed for {profile_path.stem}: {e}")
    _send(email, subject, _shell(body))
    return True


# ── D — Quota warning ─────────────────────────────────────────────────────────

def send_quota_warning(profile: dict, profile_path: Path, used: int, limit: int) -> None:
    """Sent when usage crosses 80% of the monthly quota. Once per calendar month."""
    if profile.get("is_founder"):
        return
    email = (profile.get("email") or "").strip()
    if not email:
        return
    this_month = datetime.utcnow().strftime("%Y-%m")
    if profile.get("quota_warning_sent_month") == this_month:
        return
    first = _first_name(profile)
    remaining = limit - used
    subject = "Tu approches de ta limite de vidéos ce mois-ci"
    body = (
        _h1("Tu approches de ta limite de vidéos ce mois-ci")
        + _p("Hey " + first + ",", extra_style="margin:0 0 8px")
        + _p(
            "Tu as utilisé "
            '<strong style="color:#FF7751">'
            + str(used) + " vidéo" + ("s" if used > 1 else "") + " sur " + str(limit)
            + "</strong>"
            " ce mois-ci. Il te reste seulement "
            "<strong>" + str(remaining) + " créneau" + ("x" if remaining > 1 else "") + "</strong>."
            "<br><br>Passe à un plan supérieur pour ne jamais être bloqué."
        )
        + _cta(_PRICING_URL, "Voir les plans →")
    )
    try:
        profile["quota_warning_sent_month"] = this_month
        profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[email] quota throttle stamp failed for {profile_path.stem}: {e}")
    _send(email, subject, _shell(body))
