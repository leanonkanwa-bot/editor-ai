"""Publishing Integration Engine — Feature 3.

Handles OAuth2 flows and video uploads for YouTube, TikTok, Instagram, LinkedIn.
Tokens are stored encrypted in storage/oauth_tokens.json.
Metadata is auto-generated from transcript content via Claude.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import BACKEND_DIR, settings

PUBLISH_DIR  = BACKEND_DIR / "storage"
TOKENS_FILE  = PUBLISH_DIR / "oauth_tokens.json"
HISTORY_FILE = PUBLISH_DIR / "publish_history.json"
SCHEDULED_FILE = PUBLISH_DIR / "scheduled_posts.json"

for _d in (PUBLISH_DIR,):
    _d.mkdir(parents=True, exist_ok=True)

PLATFORMS = ["youtube", "tiktok", "instagram", "linkedin"]

# OAuth2 config — credentials come from environment variables.
OAUTH_CONFIG: dict[str, dict[str, str]] = {
    "youtube": {
        "auth_url":    "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url":   "https://oauth2.googleapis.com/token",
        "scope":       "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube",
        "client_id":   os.environ.get("YOUTUBE_CLIENT_ID", ""),
        "client_secret": os.environ.get("YOUTUBE_CLIENT_SECRET", ""),
    },
    "tiktok": {
        "auth_url":    "https://www.tiktok.com/v2/auth/authorize/",
        "token_url":   "https://open.tiktokapis.com/v2/oauth/token/",
        "scope":       "video.publish,video.upload",
        "client_id":   os.environ.get("TIKTOK_CLIENT_KEY", ""),
        "client_secret": os.environ.get("TIKTOK_CLIENT_SECRET", ""),
    },
    "instagram": {
        "auth_url":    "https://www.facebook.com/v19.0/dialog/oauth",
        "token_url":   "https://graph.facebook.com/v19.0/oauth/access_token",
        "scope":       "instagram_basic,instagram_content_publish,pages_read_engagement",
        "client_id":   os.environ.get("INSTAGRAM_APP_ID", ""),
        "client_secret": os.environ.get("INSTAGRAM_APP_SECRET", ""),
    },
    "linkedin": {
        "auth_url":    "https://www.linkedin.com/oauth/v2/authorization",
        "token_url":   "https://www.linkedin.com/oauth/v2/accessToken",
        "scope":       "w_member_social,r_liteprofile",
        "client_id":   os.environ.get("LINKEDIN_CLIENT_ID", ""),
        "client_secret": os.environ.get("LINKEDIN_CLIENT_SECRET", ""),
    },
}


@dataclass
class PublishMetadata:
    title: str
    description: str
    tags: list[str] = field(default_factory=list)
    privacy: str = "public"
    scheduled_at: str | None = None
    thumbnail_path: str | None = None


@dataclass
class PublishResult:
    platform: str
    status: str          # success / error / scheduled
    post_id: str = ""
    url: str = ""
    error: str = ""
    published_at: str = ""


def _derive_key() -> bytes:
    """Derive a 32-byte key from ANTHROPIC_API_KEY for simple token encryption."""
    seed = (settings.anthropic_api_key or "leanlead-default-key").encode()
    return hashlib.sha256(seed).digest()


def _encrypt(plaintext: str) -> str:
    key = _derive_key()
    data = plaintext.encode()
    xored = bytes(b ^ key[i % 32] for i, b in enumerate(data))
    return base64.b64encode(xored).decode()


def _decrypt(ciphertext: str) -> str:
    key = _derive_key()
    data = base64.b64decode(ciphertext)
    return bytes(b ^ key[i % 32] for i, b in enumerate(data)).decode()


def _load_tokens() -> dict[str, Any]:
    if not TOKENS_FILE.exists():
        return {}
    try:
        raw = json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
        return {k: {kk: _decrypt(vv) if isinstance(vv, str) else vv
                    for kk, vv in v.items()}
                for k, v in raw.items()}
    except Exception:
        return {}


def _save_tokens(tokens: dict[str, Any]) -> None:
    encrypted = {k: {kk: _encrypt(vv) if isinstance(vv, str) else vv
                     for kk, vv in v.items()}
                 for k, v in tokens.items()}
    TOKENS_FILE.write_text(json.dumps(encrypted, indent=2), encoding="utf-8")


def _load_history() -> list[dict[str, Any]]:
    if not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_history(history: list[dict[str, Any]]) -> None:
    HISTORY_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")


def get_connections() -> list[dict[str, Any]]:
    """Return connection status for all platforms."""
    tokens = _load_tokens()
    return [
        {
            "platform":   p,
            "connected":  p in tokens and bool(tokens[p].get("access_token")),
            "configured": bool(OAUTH_CONFIG[p].get("client_id")),
        }
        for p in PLATFORMS
    ]


def get_auth_url(platform: str, redirect_uri: str) -> str:
    """Build OAuth2 authorization URL for the given platform."""
    cfg = OAUTH_CONFIG.get(platform, {})
    if not cfg.get("client_id"):
        raise ValueError(f"OAuth credentials for {platform} not configured. "
                         f"Set {platform.upper()}_CLIENT_ID/SECRET environment variables.")

    import urllib.parse
    params = {
        "client_id":     cfg["client_id"],
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         cfg["scope"],
        "state":         platform,
    }
    if platform == "youtube":
        params["access_type"] = "offline"
        params["prompt"]      = "consent"

    return cfg["auth_url"] + "?" + urllib.parse.urlencode(params)


def handle_callback(platform: str, code: str, redirect_uri: str) -> dict[str, Any]:
    """Exchange authorization code for tokens and store them."""
    import urllib.request
    import urllib.parse

    cfg = OAUTH_CONFIG.get(platform, {})
    data = urllib.parse.urlencode({
        "client_id":     cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "code":          code,
        "redirect_uri":  redirect_uri,
        "grant_type":    "authorization_code",
    }).encode()

    req = urllib.request.Request(cfg["token_url"], data=data,
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        token_data = json.loads(resp.read().decode())

    tokens = _load_tokens()
    tokens[platform] = {
        "access_token":  token_data.get("access_token", ""),
        "refresh_token": token_data.get("refresh_token", ""),
        "expires_at":    str(int(time.time()) + int(token_data.get("expires_in", 3600))),
    }
    _save_tokens(tokens)
    return {"platform": platform, "connected": True}


def revoke_connection(platform: str) -> None:
    tokens = _load_tokens()
    tokens.pop(platform, None)
    _save_tokens(tokens)


class Publisher:
    """Publishes video to specified platform."""

    def publish(
        self,
        platform: str,
        video_path: Path,
        metadata: PublishMetadata,
        job_id: str = "",
    ) -> PublishResult:
        try:
            tokens = _load_tokens()
            token = tokens.get(platform, {}).get("access_token", "")
            if not token:
                return PublishResult(platform, "error", error=f"Not connected to {platform}")

            if platform == "youtube":
                return self._publish_youtube(video_path, metadata, token, job_id)
            elif platform == "tiktok":
                return self._publish_tiktok(video_path, metadata, token, job_id)
            elif platform == "instagram":
                return self._publish_instagram(video_path, metadata, token, job_id)
            elif platform == "linkedin":
                return self._publish_linkedin(video_path, metadata, token, job_id)
            else:
                return PublishResult(platform, "error", error=f"Unknown platform: {platform}")
        except Exception as e:
            return PublishResult(platform, "error", error=str(e))

    def _publish_youtube(self, path: Path, meta: PublishMetadata, token: str, job_id: str) -> PublishResult:
        import urllib.request
        # Resumable upload init.
        metadata_body = json.dumps({
            "snippet": {
                "title":       meta.title[:100],
                "description": meta.description[:5000],
                "tags":        meta.tags[:500],
                "categoryId":  "27",  # Education
            },
            "status": {
                "privacyStatus":    meta.privacy,
                "selfDeclaredMadeForKids": False,
            },
        }).encode()

        init_req = urllib.request.Request(
            "https://www.googleapis.com/upload/youtube/v3/videos"
            "?uploadType=resumable&part=snippet,status",
            data=metadata_body,
            headers={
                "Authorization":   f"Bearer {token}",
                "Content-Type":    "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(path.stat().st_size),
            },
            method="POST",
        )
        with urllib.request.urlopen(init_req, timeout=30) as r:
            upload_url = r.headers.get("Location", "")

        if not upload_url:
            return PublishResult("youtube", "error", error="No upload URL from YouTube")

        # Upload video.
        with path.open("rb") as f:
            video_data = f.read()
        upload_req = urllib.request.Request(
            upload_url, data=video_data,
            headers={"Content-Type": "video/mp4", "Content-Length": str(len(video_data))},
            method="PUT",
        )
        with urllib.request.urlopen(upload_req, timeout=600) as r:
            resp = json.loads(r.read().decode())

        video_id = resp.get("id", "")
        url = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
        result = PublishResult("youtube", "success", post_id=video_id, url=url,
                               published_at=datetime.now(timezone.utc).isoformat())
        self._record_history(job_id, result, meta)
        return result

    def _publish_tiktok(self, path: Path, meta: PublishMetadata, token: str, job_id: str) -> PublishResult:
        import urllib.request
        size = path.stat().st_size
        # Init upload.
        init_body = json.dumps({
            "post_info": {
                "title": (meta.title + " " + " ".join(f"#{t}" for t in meta.tags[:5]))[:2200],
                "privacy_level": "PUBLIC_TO_EVERYONE" if meta.privacy == "public" else "SELF_ONLY",
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                            "chunk_size": size, "total_chunk_count": 1},
        }).encode()

        init_req = urllib.request.Request(
            "https://open.tiktokapis.com/v2/post/video/init/",
            data=init_body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
        )
        with urllib.request.urlopen(init_req, timeout=30) as r:
            init_resp = json.loads(r.read().decode())

        upload_url = init_resp.get("data", {}).get("upload_url", "")
        publish_id  = init_resp.get("data", {}).get("publish_id", "")
        if not upload_url:
            return PublishResult("tiktok", "error", error="No upload URL from TikTok")

        with path.open("rb") as f:
            video_data = f.read()
        upload_req = urllib.request.Request(
            upload_url, data=video_data,
            headers={
                "Content-Type":  "video/mp4",
                "Content-Range": f"bytes 0-{size-1}/{size}",
                "Content-Length": str(size),
            },
            method="PUT",
        )
        urllib.request.urlopen(upload_req, timeout=600)

        result = PublishResult("tiktok", "success", post_id=publish_id,
                               url="https://www.tiktok.com",
                               published_at=datetime.now(timezone.utc).isoformat())
        self._record_history(job_id, result, meta)
        return result

    def _publish_instagram(self, path: Path, meta: PublishMetadata, token: str, job_id: str) -> PublishResult:
        # Instagram requires a public URL for the video (Graph API limitation).
        # In production, you'd upload to a CDN first.
        return PublishResult("instagram", "error",
                             error="Instagram requires a public video URL. Upload to CDN first, then use /api/publish/instagram/from-url.")

    def _publish_linkedin(self, path: Path, meta: PublishMetadata, token: str, job_id: str) -> PublishResult:
        import urllib.request
        # Step 1: Register upload.
        reg_body = json.dumps({
            "registerUploadRequest": {
                "recipes":         ["urn:li:digitalmediaRecipe:feedshare-video"],
                "owner":           "urn:li:person:me",
                "serviceRelationships": [{
                    "relationshipType": "OWNER",
                    "identifier":       "urn:li:userGeneratedContent",
                }],
            }
        }).encode()
        reg_req = urllib.request.Request(
            "https://api.linkedin.com/v2/assets?action=registerUpload",
            data=reg_body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(reg_req, timeout=30) as r:
            reg_resp = json.loads(r.read().decode())

        upload_url = (reg_resp.get("value", {})
                      .get("uploadMechanism", {})
                      .get("com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest", {})
                      .get("uploadUrl", ""))
        asset = reg_resp.get("value", {}).get("asset", "")
        if not upload_url:
            return PublishResult("linkedin", "error", error="No upload URL from LinkedIn")

        # Step 2: Upload binary.
        with path.open("rb") as f:
            video_data = f.read()
        up_req = urllib.request.Request(
            upload_url, data=video_data,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"},
            method="PUT",
        )
        urllib.request.urlopen(up_req, timeout=600)

        # Step 3: Create post.
        post_body = json.dumps({
            "author":      "urn:li:person:me",
            "lifecycleState": "PUBLISHED",
            "specificContent": {"com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": meta.description[:700]},
                "shareMediaCategory": "VIDEO",
                "media": [{"status": "READY", "media": asset,
                           "title": {"text": meta.title[:200]}}],
            }},
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }).encode()
        post_req = urllib.request.Request(
            "https://api.linkedin.com/v2/ugcPosts",
            data=post_body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                     "X-Restli-Protocol-Version": "2.0.0"},
        )
        with urllib.request.urlopen(post_req, timeout=30) as r:
            post_id = r.headers.get("x-restli-id", "")

        result = PublishResult("linkedin", "success", post_id=post_id,
                               url=f"https://www.linkedin.com/feed/update/{post_id}/",
                               published_at=datetime.now(timezone.utc).isoformat())
        self._record_history(job_id, result, meta)
        return result

    def _record_history(self, job_id: str, result: PublishResult, meta: PublishMetadata) -> None:
        history = _load_history()
        history.append({
            "job_id":       job_id,
            "platform":     result.platform,
            "post_id":      result.post_id,
            "url":          result.url,
            "title":        meta.title,
            "published_at": result.published_at,
            "status":       result.status,
        })
        _save_history(history)


def generate_metadata(
    platform: str,
    transcript_text: str,
    hook_rewrite: str = "",
    plan: dict | None = None,
) -> PublishMetadata:
    """Auto-generate platform-specific metadata from transcript using Claude."""
    from anthropic import Anthropic
    client = Anthropic(api_key=settings.anthropic_api_key)

    platform_instructions = {
        "youtube": (
            "Generate YouTube video metadata:\n"
            "- title: compelling, max 100 chars, SEO-optimised\n"
            "- description: 3-paragraph summary + 5 SEO keywords + CTA + timestamps placeholder\n"
            "- tags: 10 relevant tags (array of strings)\n"
            "- category: 'Education' or 'HowTo' or 'Business'\n"
        ),
        "tiktok": (
            "Generate TikTok caption:\n"
            "- title: hook + main point, max 150 chars\n"
            "- description: same as title\n"
            "- tags: 5 trending relevant hashtags WITHOUT the # sign (array)\n"
        ),
        "instagram": (
            "Generate Instagram caption:\n"
            "- title: hook sentence\n"
            "- description: hook + 3 sentences + 15 relevant hashtags (include # sign in description)\n"
            "- tags: 15 hashtags WITHOUT # sign (array)\n"
        ),
        "linkedin": (
            "Generate LinkedIn post:\n"
            "- title: professional insight title\n"
            "- description: professional rewrite of key insight, max 700 chars, max 3 hashtags\n"
            "- tags: 3 professional hashtags WITHOUT # sign (array)\n"
        ),
    }

    prompt = (
        f"{platform_instructions.get(platform, platform_instructions['youtube'])}\n\n"
        f"HOOK: {hook_rewrite or 'N/A'}\n"
        f"TRANSCRIPT (first 600 chars): {transcript_text[:600]}\n\n"
        "Reply ONLY with JSON:\n"
        '{"title":"...","description":"...","tags":["..."]}'
    )

    try:
        resp = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end+1]) if start != -1 else {}
        return PublishMetadata(
            title=str(data.get("title", hook_rewrite or "New Video")),
            description=str(data.get("description", transcript_text[:500])),
            tags=[str(t) for t in data.get("tags", [])[:20]],
        )
    except Exception:
        return PublishMetadata(
            title=hook_rewrite or "New Video",
            description=transcript_text[:500],
            tags=[],
        )
