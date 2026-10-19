"""Exercise the read-only delivery readiness helper."""

import importlib.util
import contextlib
import io
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "delivery_check", ROOT / "skills/github-workflow/scripts/delivery-check.py"
)
DELIVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DELIVERY)
HEAD = "a" * 40
REPO = "fixture/repository"
REVIEW = "- Independent review: done on " + HEAD + "; findings: none"


class Result:
    def __init__(self, code=0, stdout=""):
        self.returncode = code
        self.stdout = stdout
        self.stderr = ""


def view(body=REVIEW, **overrides):
    data = {
        "state": "OPEN",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "reviewDecision": "APPROVED",
        "headRefOid": HEAD,
        "baseRefName": "main",
        "body": body,
        "url": "https://github.com/fixture/repository/pull/1",
    }
    data.update(overrides)
    return data


def runner(
    pr_view=None,
    checks=None,
    all_checks=None,
    threads=None,
    merge_base=0,
    cat_file=0,
    view_failure=False,
    checks_exit=0,
):
    pr_view = view() if pr_view is None else pr_view
    checks = [{"name": "verify", "state": "SUCCESS", "bucket": "pass"}] if checks is None else checks
    all_checks = checks if all_checks is None else all_checks
    threads = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}} if threads is None else threads

    def execute(args):
        if args[:3] == ["gh", "pr", "view"]:
            return Result(1 if view_failure else 0, json.dumps(pr_view))
        if args[:3] == ["gh", "pr", "checks"]:
            if "--required" not in args:
                return Result(0, json.dumps(all_checks))
            return Result(checks_exit, json.dumps(checks) if checks_exit == 0 else "no required checks reported")
        if args[:3] == ["gh", "api", "graphql"]:
            return Result(0, json.dumps(threads))
        if args[:2] == ["git", "cat-file"]:
            return Result(cat_file)
        if args[:2] == ["git", "merge-base"]:
            return Result(merge_base)
        raise AssertionError("unexpected command: " + repr(args))

    return execute


class DeliveryCheckTests(unittest.TestCase):
    def test_ready(self):
        result = DELIVERY.check_pr("1", REPO, "main", runner())
        self.assertTrue(result["ready"])
        self.assertEqual(result["blockers"], [])

    def test_stale_head_is_blocked(self):
        result = DELIVERY.check_pr("1", REPO, "main", runner(merge_base=1))
        self.assertFalse(result["ready"])
        self.assertIn("PR head is not current with the verified default branch", result["blockers"])

    def test_missing_review_line_is_blocked(self):
        result = DELIVERY.check_pr("1", REPO, "main", runner(pr_view=view("Why / What / Evidence")))
        self.assertFalse(result["ready"])
        self.assertIn("PR body has no valid Independent review line", result["blockers"])

    def test_unresolved_thread_is_blocked(self):
        threads = {
            "data": {
                "repository": {
                    "pullRequest": {"reviewThreads": {"nodes": [{"isResolved": False}]}}
                }
            }
        }
        result = DELIVERY.check_pr("1", REPO, "main", runner(threads=threads))
        self.assertFalse(result["ready"])
        self.assertIn("1 unresolved review thread(s)", result["blockers"])

    def test_no_required_checks_is_a_warning(self):
        result = DELIVERY.check_pr("1", REPO, "main", runner(checks=[]))
        self.assertTrue(result["ready"])
        self.assertIn("no required checks reported", result["warnings"])

    def test_no_required_checks_exit_one_is_a_warning(self):
        result = DELIVERY.check_pr("1", REPO, "main", runner(checks=[], checks_exit=1, all_checks=[{"name": "verify", "state": "SUCCESS", "bucket": "pass"}]))
        self.assertTrue(result["ready"])
        self.assertIn("no required checks reported; evaluating all reported checks", result["warnings"])

    def test_no_required_checks_with_failed_reported_check_is_blocked(self):
        result = DELIVERY.check_pr(
            "1",
            REPO,
            "main",
            runner(checks=[], checks_exit=1, all_checks=[{"name": "verify", "state": "FAILURE", "bucket": "fail"}]),
        )
        self.assertFalse(result["ready"])
        self.assertIn("required check is not green: verify (fail)", result["blockers"])

    def test_blocked_merge_state_is_blocked(self):
        result = DELIVERY.check_pr("1", REPO, "main", runner(pr_view=view(mergeStateStatus="BLOCKED")))
        self.assertFalse(result["ready"])
        self.assertIn("merge state is not ready: BLOCKED", result["blockers"])

    def test_review_required_is_blocked(self):
        result = DELIVERY.check_pr("1", REPO, "main", runner(pr_view=view(reviewDecision="REVIEW_REQUIRED")))
        self.assertFalse(result["ready"])
        self.assertIn("review decision is REVIEW_REQUIRED", result["blockers"])

    def test_changes_requested_is_blocked(self):
        result = DELIVERY.check_pr("1", REPO, "main", runner(pr_view=view(reviewDecision="CHANGES_REQUESTED")))
        self.assertFalse(result["ready"])
        self.assertIn("review decision is CHANGES_REQUESTED", result["blockers"])

    def test_to_fill_is_blocked(self):
        result = DELIVERY.check_pr("1", REPO, "main", runner(pr_view=view(REVIEW + "\nTO FILL")))
        self.assertFalse(result["ready"])
        self.assertIn("PR body still contains TO FILL", result["blockers"])

    def test_failed_required_check_is_blocked(self):
        checks = [{"name": "verify", "state": "FAILURE", "bucket": "fail"}]
        result = DELIVERY.check_pr("1", REPO, "main", runner(checks=checks))
        self.assertFalse(result["ready"])
        self.assertIn("required check is not green: verify (fail)", result["blockers"])

    def test_invalid_head_is_blocked(self):
        result = DELIVERY.check_pr("1", REPO, "main", runner(pr_view=view(headRefOid="short")))
        self.assertFalse(result["ready"])
        self.assertIn("PR head is not a full commit SHA", result["blockers"])

    def test_pr_url_selector(self):
        self.assertEqual(DELIVERY.pr_number("https://github.com/fixture/repository/pull/12"), "12")
        self.assertEqual(DELIVERY.pr_number("12"), "12")

    def test_gh_failure_exits_two(self):
        with self.assertRaises(SystemExit) as error, contextlib.redirect_stdout(io.StringIO()):
            DELIVERY.check_pr("1", REPO, "main", runner(view_failure=True))
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
