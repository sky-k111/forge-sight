"""报告领域状态与不可变边界。"""

import pytest

from app.reports.domain import (
    ReportSectionStatus,
    ReportVersionStatus,
    InvalidReportTransition,
    PublishedVersionImmutable,
    ensure_version_mutable,
    transition_report_status,
)
from app.reports.contracts import SubscriptionCreate


def test_report_can_reach_published_without_manual_review_state():
    status = "queued"
    for target in (
        "checking_data",
        "planning",
        "running_sections",
        "validating",
        "composing",
        "published",
    ):
        status = transition_report_status(status, target)

    assert status == "published"


def test_report_rejects_illegal_transition_after_publication():
    with pytest.raises(InvalidReportTransition):
        transition_report_status("published", "running_sections")


def test_published_version_content_is_immutable():
    with pytest.raises(PublishedVersionImmutable):
        ensure_version_mutable("published")


def test_unpublished_version_remains_mutable():
    ensure_version_mutable("composing")


def test_new_subscription_contract_defaults_to_email_auto_publish():
    subscription = SubscriptionCreate(name="经营周报", templateId="tpl-weekly", sourceId=1)

    assert subscription.channel == "email"
    assert subscription.publishPolicy == "auto_publish"


def test_version_and_section_states_are_centralized():
    assert ReportVersionStatus.PUBLISHED == "published"
    assert ReportSectionStatus.SUCCEEDED == "succeeded"
