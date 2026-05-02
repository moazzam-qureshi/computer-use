"""Take an approved Order; run the apply form sequence; mark submitted/failed."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from substrate import act
from upwork import apply_form
from storage.orders import OrderStore
from storage.connects_ledger import ConnectsLedgerStore
from domain.humanization import Humanizer
from domain.types import Order
from scheduler.failure_pings import (
    LoginExpired, CloudflareWall, ApplyFormChanged, OrderAlreadySubmitted, ConnectsExhausted,
)


def execute_approved_order(
    order: Order,
    job_url: str,
    *,
    order_store: OrderStore,
    connects_ledger: ConnectsLedgerStore,
    humanizer: Humanizer,
    bid_amount_usd: float,
    connects_cost: int = 16,
    really_submit: bool = False,
) -> None:
    if order.status == "submitted":
        raise OrderAlreadySubmitted(f"order {order.order_id}")

    order_store.update_status(order.order_id, "staging")
    apply_form.navigate_to_apply(window_title="Upwork", job_url=job_url)
    state = apply_form.wait_for_form_or_login(window_title="Upwork", timeout_s=30)
    if state == "login_required":
        order_store.update_status(order.order_id, "failed")
        raise LoginExpired(f"navigated to {job_url}")
    if state == "cloudflare":
        order_store.update_status(order.order_id, "failed")
        raise CloudflareWall(f"navigated to {job_url}")

    pauses = humanizer.sample_apply_form_pauses()
    time.sleep(pauses["initial_idle"])
    apply_form.paste_cover_letter(window_title="Upwork", text=order.cover_letter_body or "")
    time.sleep(pauses["after_paste"])

    if order.screening_answers_json:
        time.sleep(pauses["before_screening"])
        apply_form.answer_screening_questions(window_title="Upwork", answers=order.screening_answers_json)

    apply_form.select_never_for_rate_increase(window_title="Upwork")
    apply_form.fill_bid_amount(window_title="Upwork", amount_usd=bid_amount_usd)
    time.sleep(pauses["final_review"])

    order_store.update_status(order.order_id, "attempting")

    if not really_submit:
        order_store.update_status(order.order_id, "awaiting_approval")
        order.status = "awaiting_approval"
        order.failed_reason = "really_submit=False (Phase 1 default)"
        return

    apply_form.submit_proposal(window_title="Upwork")
    order_store.mark_submitted(order.order_id)
    connects_ledger.record(delta=-connects_cost, reason=f"order {order.order_id}", balance_after=None, order_id=order.order_id)
