"""Pure-function tests for the downstream-attribution inheritance helpers.

Exercises `is_campaign_utm` and the `referrer_utm=` extension to
`pick_attribution_utm`, which together let multi-hop referral chains stay
attributed to the campaign root (e.g. RuTracker launch tag) instead of
collapsing to the generic `partner` marker on each hop.
"""

from __future__ import annotations

import pytest

from bloobcat.funcs.referral_attribution import (
    PARTNER_SOURCE_UTM,
    is_campaign_utm,
    pick_attribution_utm,
)


# --- is_campaign_utm ----------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "qr_rt_launch_2026_05",
        "qr_partnerXyZ",
        "ig_story_2026_05",
        "google_ads_2026_05",
        "Custom_Tag",
    ],
)
def test_is_campaign_utm_true_for_specific_tags(value: str) -> None:
    assert is_campaign_utm(value) is True


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", PARTNER_SOURCE_UTM, "  partner  "],
)
def test_is_campaign_utm_false_for_empty_or_partner(value: str | None) -> None:
    assert is_campaign_utm(value) is False


# --- pick_attribution_utm: backwards compatibility (no referrer_utm) ----------


def test_pick_no_inputs_returns_none() -> None:
    assert pick_attribution_utm(None, None) is None
    assert pick_attribution_utm("", "") is None


def test_pick_keeps_current_when_present() -> None:
    assert pick_attribution_utm("qr_x", "ig_y") == "qr_x"


def test_pick_uses_incoming_when_current_empty() -> None:
    assert pick_attribution_utm(None, "ig_y") == "ig_y"


def test_pick_force_partner_replaces_blank_current() -> None:
    assert (
        pick_attribution_utm(None, PARTNER_SOURCE_UTM, force_partner_source=True)
        == PARTNER_SOURCE_UTM
    )


def test_pick_force_partner_preserves_qr_token_over_partner() -> None:
    assert (
        pick_attribution_utm("qr_existing", PARTNER_SOURCE_UTM, force_partner_source=True)
        == "qr_existing"
    )


# --- pick_attribution_utm: referrer_utm inheritance ---------------------------


def test_inherits_when_resolved_is_partner_marker() -> None:
    """First-degree friend of a RuTracker user should inherit the campaign tag."""
    result = pick_attribution_utm(
        None,
        PARTNER_SOURCE_UTM,
        force_partner_source=True,
        referrer_utm="qr_rt_launch_2026_05",
    )
    assert result == "qr_rt_launch_2026_05"


def test_inherits_when_resolved_is_blank() -> None:
    """No incoming utm + referrer with campaign tag => inherit."""
    result = pick_attribution_utm(
        None,
        None,
        referrer_utm="qr_rt_launch_2026_05",
    )
    assert result == "qr_rt_launch_2026_05"


def test_does_not_overwrite_existing_campaign_tag() -> None:
    """A user with their own campaign tag must NOT be overridden by referrer."""
    result = pick_attribution_utm(
        "qr_user_first_campaign",
        None,
        referrer_utm="qr_rt_launch_2026_05",
    )
    assert result == "qr_user_first_campaign"


def test_does_not_inherit_partner_only_referrer() -> None:
    """If the referrer has only the generic `partner` marker, do not inherit."""
    result = pick_attribution_utm(
        None,
        PARTNER_SOURCE_UTM,
        force_partner_source=True,
        referrer_utm=PARTNER_SOURCE_UTM,
    )
    assert result == PARTNER_SOURCE_UTM


def test_does_not_inherit_when_referrer_blank() -> None:
    result = pick_attribution_utm(
        None,
        PARTNER_SOURCE_UTM,
        force_partner_source=True,
        referrer_utm=None,
    )
    assert result == PARTNER_SOURCE_UTM


def test_inherits_with_incoming_partner_no_force() -> None:
    """Even without force flag, if resolved utm is just `partner`, inherit campaign root."""
    result = pick_attribution_utm(
        None,
        PARTNER_SOURCE_UTM,
        referrer_utm="qr_rt_launch_2026_05",
    )
    assert result == "qr_rt_launch_2026_05"


def test_multi_hop_chain_via_repeated_application() -> None:
    """Simulate three hops: A (rutracker root) -> B -> C -> D, each pick yields the root."""
    root = "qr_rt_launch_2026_05"
    # Hop 1: B is invited by A. B has no prior utm, comes via partner-A_id.
    b_utm = pick_attribution_utm(
        None, PARTNER_SOURCE_UTM, force_partner_source=True, referrer_utm=root
    )
    assert b_utm == root
    # Hop 2: C is invited by B. C inherits from B (whose utm is now root).
    c_utm = pick_attribution_utm(
        None, PARTNER_SOURCE_UTM, force_partner_source=True, referrer_utm=b_utm
    )
    assert c_utm == root
    # Hop 3: D inherits from C.
    d_utm = pick_attribution_utm(
        None, PARTNER_SOURCE_UTM, force_partner_source=True, referrer_utm=c_utm
    )
    assert d_utm == root


def test_inherits_into_user_with_existing_partner_marker() -> None:
    """User who currently has just `partner` should be upgraded to campaign root."""
    result = pick_attribution_utm(
        PARTNER_SOURCE_UTM,
        None,
        referrer_utm="qr_rt_launch_2026_05",
    )
    assert result == "qr_rt_launch_2026_05"


def test_inherits_strips_whitespace_in_referrer_utm() -> None:
    result = pick_attribution_utm(
        None,
        PARTNER_SOURCE_UTM,
        force_partner_source=True,
        referrer_utm="  qr_rt_launch_2026_05  ",
    )
    assert result == "qr_rt_launch_2026_05"
