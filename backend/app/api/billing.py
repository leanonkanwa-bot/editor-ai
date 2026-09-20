"""Stripe Checkout billing flow.

Flow:
  1. POST /api/billing/checkout {tier, profile_id} -> {url}
     Frontend redirects the browser to the returned Stripe-hosted URL.
  2. User pays on Stripe. Stripe redirects back to /app?billing=success
     or /app?billing=cancelled.
  3. Stripe also calls POST /api/billing/webhook (server-to-server) with a
     checkout.session.completed event. We verify the signature, then mark
     the profile (identified by client_reference_id=profile_id) as paid.

The tier that gets activated comes from server-set session metadata, not
from anything the client sends to the webhook — the webhook only trusts
Stripe-signed payloads.
"""

from __future__ import annotations

import json
import logging

import stripe
from fastapi import APIRouter, Body, HTTPException, Request

from app.core.session import caller_profile
from app.api.jobs import store as job_store
from app.core.config import settings
from app.core.plans import DEFAULT_PLAN, effective_plan_info, has_4k_access

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing", tags=["billing"])

_PROFILES_DIR = settings._data_root / "profiles"

_TIER_PRICE_IDS = {
    "starter": lambda: settings.stripe_price_starter,
    "pro": lambda: settings.stripe_price_pro,
    "agency": lambda: settings.stripe_price_agency,
}


@router.get("/usage/{profile_id}")
def get_usage(profile_id: str, request: Request) -> dict:
    """Usage of the signed-in user's own profile."""
    if caller_profile(request) != profile_id:
        raise HTTPException(404, "Profile not found")
    profile: dict = {}
    profile_path = _PROFILES_DIR / f"{profile_id}.json"
    if profile_path.exists():
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    info = effective_plan_info(profile)
    used = job_store.count_for_profile(profile_id, info["period"])
    return {
        "plan": profile.get("plan") or DEFAULT_PLAN,
        "plan_label": info["label"],
        "used": used,
        "limit": info["limit"],
        "period": info["period"],
        "exceeded": used >= info["limit"],
        "has_4k": has_4k_access(profile),
    }


@router.post("/checkout")
async def create_checkout_session(request: Request, payload: dict = Body(...)) -> dict:
    tier = (payload.get("tier") or "").strip().lower()
    profile_id = payload.get("profile_id") or ""

    if tier not in _TIER_PRICE_IDS:
        raise HTTPException(400, f"Unknown tier: {tier!r}")
    if not settings.stripe_secret_key:
        raise HTTPException(503, "Billing is not configured (STRIPE_SECRET_KEY missing)")
    price_id = _TIER_PRICE_IDS[tier]()
    if not price_id:
        raise HTTPException(503, f"No Stripe price configured for tier {tier!r}")

    stripe.api_key = settings.stripe_secret_key
    base = str(request.base_url).rstrip("/")

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            client_reference_id=profile_id or None,
            metadata={"tier": tier, "profile_id": profile_id},
            success_url=f"{base}/app?billing=success",
            cancel_url=f"{base}/app?billing=cancelled",
        )
    except stripe.error.StripeError as e:
        raise HTTPException(502, f"Stripe error: {e.user_message or str(e)}")

    return {"url": session.url}


@router.post("/webhook")
async def stripe_webhook(request: Request) -> dict:
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not settings.stripe_webhook_secret:
        logger.warning("Stripe webhook received but STRIPE_WEBHOOK_SECRET is not set")
        raise HTTPException(503, "Webhook not configured")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(400, "Invalid webhook signature")

    obj = event["data"]["object"]
    if event["type"] == "checkout.session.completed":
        _handle_checkout_completed(obj)
    elif event["type"] == "customer.subscription.updated":
        _handle_subscription_updated(obj)
    elif event["type"] == "customer.subscription.deleted":
        _handle_subscription_deleted(obj)

    return {"received": True}


def _find_profile_by_customer_id(customer_id: str) -> tuple[str | None, dict | None]:
    """Scan profiles dir for a profile whose stripe_customer_id matches."""
    if not customer_id:
        return None, None
    try:
        for p in _PROFILES_DIR.glob("*.json"):
            try:
                profile = json.loads(p.read_text(encoding="utf-8"))
                if profile.get("stripe_customer_id") == customer_id:
                    return p.stem, profile
            except Exception:
                continue
    except Exception:
        pass
    return None, None


def _save_profile(profile_id: str, profile: dict) -> None:
    (_PROFILES_DIR / f"{profile_id}.json").write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _handle_subscription_updated(subscription: dict) -> None:
    customer_id = subscription.get("customer", "")
    status = subscription.get("status", "")
    cancel_at_period_end = subscription.get("cancel_at_period_end", False)
    current_period_end = subscription.get("current_period_end")

    profile_id, profile = _find_profile_by_customer_id(customer_id)
    if not profile_id:
        logger.warning(f"subscription.updated: no profile found for customer={customer_id}")
        return

    if status == "canceled":
        profile["plan"] = DEFAULT_PLAN
        profile["billing_status"] = "canceled"
        profile["cancel_at"] = None
        _save_profile(profile_id, profile)
        logger.info(f"subscription.updated status=canceled → plan=free for profile={profile_id}")
    elif status == "active" and cancel_at_period_end:
        profile["billing_status"] = "canceling"
        profile["cancel_at"] = current_period_end
        _save_profile(profile_id, profile)
        logger.info(
            f"subscription.updated cancel_at_period_end=True → "
            f"billing_status=canceling cancel_at={current_period_end} profile={profile_id}"
        )
    else:
        logger.info(
            f"subscription.updated status={status} cancel_at_period_end={cancel_at_period_end} "
            f"— no action for profile={profile_id}"
        )


def _handle_subscription_deleted(subscription: dict) -> None:
    customer_id = subscription.get("customer", "")
    profile_id, profile = _find_profile_by_customer_id(customer_id)
    if not profile_id:
        logger.warning(f"subscription.deleted: no profile found for customer={customer_id}")
        return
    profile["plan"] = DEFAULT_PLAN
    profile["billing_status"] = "canceled"
    profile["cancel_at"] = None
    _save_profile(profile_id, profile)
    logger.info(f"subscription.deleted → plan=free for profile={profile_id}")


def _handle_checkout_completed(session: dict) -> None:
    profile_id = session.get("client_reference_id") or session.get("metadata", {}).get("profile_id")
    tier = session.get("metadata", {}).get("tier")

    if not profile_id:
        logger.warning("checkout.session.completed with no profile_id/client_reference_id; skipping")
        return

    profile_path = _PROFILES_DIR / f"{profile_id}.json"
    if not profile_path.exists():
        logger.warning(f"checkout.session.completed for unknown profile_id={profile_id}")
        return

    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning(f"Corrupt profile file for profile_id={profile_id}, skipping billing update")
        return

    profile["plan"] = tier
    profile["billing_status"] = "active"
    profile["stripe_customer_id"] = session.get("customer")
    profile["stripe_subscription_id"] = session.get("subscription")
    profile_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"Activated plan={tier} for profile_id={profile_id}")
