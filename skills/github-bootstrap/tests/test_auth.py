"""Network-free tests for the GitHub authorization continuation state machine."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import auth  # noqa: E402


def intent(operation: str = "SYNC") -> dict[str, str]:
    return {
        "operation": operation,
        "owner": "example-owner",
        "repository": "continuity",
        "branch": "main",
    }


def observation(**overrides) -> dict:
    result = {
        "integration": True,
        "online": True,
        "identity": "example-owner",
        "expected_owner": "example-owner",
        "repo_http": None,
        "repo_access": True,
        "owner_repository_listing_complete": False,
        "owner_repository_present": None,
        "repo_absence_authoritative": False,
        "auth_denied": False,
    }
    result.update(overrides)
    return result


class AuthorizationStateTests(unittest.TestCase):
    def test_authorized_preserves_each_supported_operation_and_branch(self):
        for operation in ("READ", "SYNC", "BACKUP"):
            with self.subTest(operation=operation):
                requested = intent(operation)
                result = auth.evaluate(requested, observation())
                self.assertEqual(result["status"], "AUTHORIZED")
                self.assertEqual(result["intent"], requested)
                self.assertEqual(result["next_action"], "continue_operation")
                self.assertTrue(result["can_continue"])
                self.assertFalse(result["resumable"])

    def test_successful_authenticated_http_response_confirms_access(self):
        result = auth.evaluate(
            intent(), observation(repo_http=200, repo_access=None)
        )
        self.assertEqual(result["status"], "AUTHORIZED")

    def test_no_integration_is_recoverable_through_the_official_connector(self):
        result = auth.evaluate(intent(), observation(integration=False))
        self.assertEqual(result["status"], "NO_GITHUB_INTEGRATION")
        self.assertEqual(result["next_action"], "connect_official_platform_github")
        self.assertTrue(result["resumable"])

    def test_offline_is_recoverable(self):
        result = auth.evaluate(intent(), observation(online=False))
        self.assertEqual(result["status"], "OFFLINE")
        self.assertTrue(result["resumable"])

    def test_missing_identity_requires_official_authorization(self):
        result = auth.evaluate(intent(), observation(identity=None))
        self.assertEqual(result["status"], "AUTH_REQUIRED")
        self.assertEqual(
            result["next_action"], "authorize_with_official_platform_github"
        )
        self.assertTrue(result["resumable"])

    def test_http_401_requires_authorization(self):
        result = auth.evaluate(intent(), observation(repo_http=401))
        self.assertEqual(result["status"], "AUTH_REQUIRED")
        self.assertTrue(result["resumable"])

    def test_refused_authorization_stops_without_reprompt_or_resume(self):
        result = auth.evaluate(intent(), observation(auth_denied=True))
        self.assertEqual(result["status"], "AUTH_REQUIRED")
        self.assertEqual(result["next_action"], "stop")
        self.assertFalse(result["resumable"])
        with self.assertRaisesRegex(ValueError, "cannot be resumed"):
            auth.resume(result, observation())

    def test_wrong_account_can_be_corrected_without_losing_intent(self):
        requested = intent("BACKUP")
        pending = auth.evaluate(requested, observation(identity="other-user"))
        self.assertEqual(pending["status"], "WRONG_ACCOUNT")
        resumed = auth.resume(pending, observation())
        self.assertEqual(resumed["status"], "AUTHORIZED")
        self.assertEqual(resumed["intent"], requested)

    def test_403_is_forbidden_and_never_offers_repository_creation(self):
        result = auth.evaluate(
            intent(),
            observation(
                repo_http=403,
                repo_access=False,
                owner_repository_listing_complete=True,
                owner_repository_present=False,
                repo_absence_authoritative=True,
            ),
        )
        self.assertEqual(result["status"], "FORBIDDEN")
        self.assertEqual(result["next_action"], "stop")
        self.assertFalse(result["may_request_private_creation"])
        self.assertFalse(result["can_continue"])

    def test_known_wrong_identity_precedes_403_and_never_offers_creation(self):
        for status in (401, 403):
            with self.subTest(repo_http=status):
                result = auth.evaluate(
                    intent(),
                    observation(
                        identity="other-user",
                        repo_http=status,
                        repo_access=False,
                        owner_repository_listing_complete=True,
                        owner_repository_present=False,
                        repo_absence_authoritative=True,
                    ),
                )
                self.assertEqual(result["status"], "WRONG_ACCOUNT")
                self.assertFalse(result["can_continue"])
                self.assertFalse(result["may_request_private_creation"])

    def test_404_and_complete_listing_absence_are_not_authoritative(self):
        for evidence in (
            observation(
                repo_http=404,
                repo_access=False,
                owner_repository_listing_complete=False,
                owner_repository_present=False,
            ),
            observation(
                repo_http=404,
                repo_access=False,
                owner_repository_listing_complete=True,
                owner_repository_present=False,
            ),
            observation(
                repo_http=None,
                repo_access=False,
                owner_repository_listing_complete=True,
                owner_repository_present=False,
            ),
        ):
            with self.subTest(evidence=evidence):
                result = auth.evaluate(intent(), evidence)
                self.assertEqual(result["status"], "UNKNOWN_REPOSITORY_ACCESS")
                self.assertFalse(result["may_request_private_creation"])

    def test_independent_authoritative_absence_allows_not_found(self):
        result = auth.evaluate(
            intent(),
            observation(
                repo_http=404,
                repo_access=False,
                owner_repository_listing_complete=False,
                owner_repository_present=None,
                repo_absence_authoritative=True,
            ),
        )
        self.assertEqual(result["status"], "REPO_NOT_FOUND")
        self.assertTrue(result["may_request_private_creation"])
        self.assertFalse(result["can_continue"])

    def test_authoritative_absence_without_http_404_never_means_not_found(self):
        for status in (None, 401, 403, 429, 500):
            with self.subTest(repo_http=status):
                result = auth.evaluate(
                    intent(),
                    observation(
                        repo_http=status,
                        repo_access=False,
                        owner_repository_listing_complete=True,
                        owner_repository_present=False,
                        repo_absence_authoritative=True,
                    ),
                )
                self.assertNotEqual(result["status"], "REPO_NOT_FOUND")
                self.assertFalse(result["may_request_private_creation"])

    def test_authoritative_absence_does_not_prove_missing_for_another_owner(self):
        requested = intent()
        requested["owner"] = "target-organization"
        result = auth.evaluate(
            requested,
            observation(
                identity="example-owner",
                expected_owner="example-owner",
                repo_access=False,
                repo_http=404,
                repo_absence_authoritative=True,
            ),
        )
        self.assertEqual(result["status"], "UNKNOWN_REPOSITORY_ACCESS")

    def test_wrong_identity_cannot_use_authoritative_absence_for_target(self):
        result = auth.evaluate(
            intent(),
            observation(
                identity="other-user",
                owner_repository_listing_complete=True,
                owner_repository_present=False,
                repo_absence_authoritative=True,
            ),
        )
        self.assertEqual(result["status"], "WRONG_ACCOUNT")
        self.assertFalse(result["may_request_private_creation"])

    def test_listing_absence_without_authority_and_listing_presence_stay_unknown(self):
        for evidence in (
            observation(
                owner_repository_listing_complete=False,
                owner_repository_present=False,
                repo_access=False,
            ),
            observation(
                owner_repository_listing_complete=True,
                owner_repository_present=True,
                repo_access=False,
            ),
            observation(
                owner_repository_listing_complete=True,
                owner_repository_present=True,
                repo_access=False,
                repo_absence_authoritative=True,
            ),
        ):
            with self.subTest(evidence=evidence):
                result = auth.evaluate(intent(), evidence)
                self.assertEqual(result["status"], "UNKNOWN_REPOSITORY_ACCESS")
                self.assertFalse(result["may_request_private_creation"])

    def test_contradictory_access_evidence_is_unknown(self):
        result = auth.evaluate(
            intent(),
            observation(
                repo_http=200,
                repo_access=False,
                owner_repository_listing_complete=True,
                owner_repository_present=False,
            ),
        )
        self.assertEqual(result["status"], "UNKNOWN_REPOSITORY_ACCESS")

    def test_other_http_errors_and_unverified_denial_remain_unknown(self):
        for evidence in (
            observation(repo_http=500, repo_access=None),
            observation(repo_http=None, repo_access=False),
        ):
            with self.subTest(evidence=evidence):
                result = auth.evaluate(intent(), evidence)
                self.assertEqual(result["status"], "UNKNOWN_REPOSITORY_ACCESS")

    def test_pending_authorization_resumes_all_operations_with_same_intent(self):
        for operation in ("READ", "SYNC", "BACKUP"):
            with self.subTest(operation=operation):
                requested = intent(operation)
                pending = auth.evaluate(requested, observation(identity=None))
                resumed = auth.resume(pending, observation(repo_access=True))
                self.assertEqual(resumed["status"], "AUTHORIZED")
                self.assertEqual(resumed["intent"], requested)
                self.assertTrue(resumed["can_continue"])

    def test_unsupported_environment_preserves_intent_for_recovery(self):
        requested = intent("READ")
        pending = auth.unsupported_environment(requested)
        resumed = auth.resume(pending, observation())
        self.assertEqual(resumed["status"], "AUTHORIZED")
        self.assertEqual(resumed["intent"], requested)

    def test_every_result_uses_an_allowed_status(self):
        results = [
            auth.evaluate(intent(), observation()),
            auth.evaluate(intent(), observation(identity=None)),
            auth.evaluate(intent(), observation(integration=False)),
            auth.evaluate(intent(), observation(online=False)),
            auth.evaluate(intent(), observation(repo_http=403)),
            auth.evaluate(intent(), observation(repo_http=404, repo_access=False)),
            auth.unsupported_environment(intent()),
        ]
        self.assertTrue(all(result["status"] in auth.STATUSES for result in results))


class ContractValidationTests(unittest.TestCase):
    def test_rejects_unsupported_or_malformed_intent(self):
        for requested in (
            {"operation": "DELETE", "owner": "example-owner", "repository": "repo"},
            {"operation": [], "owner": "example-owner", "repository": "repo"},
            {"operation": "READ", "owner": "", "repository": "repo"},
            {"operation": "READ", "owner": "example-owner", "repository": "repo", "token": "x"},
            {"operation": "READ", "owner": "example-owner", "repository": "repo", "branch": ""},
        ):
            with self.subTest(intent=requested):
                with self.assertRaises(ValueError):
                    auth.evaluate(requested, observation())

    def test_rejects_missing_extra_and_mistyped_observation_fields(self):
        cases = []
        missing = observation()
        del missing["auth_denied"]
        cases.append(missing)
        extra = observation(token="secret")
        cases.append(extra)
        mistyped = observation(integration=1)
        cases.append(mistyped)
        bad_status = observation(repo_http=True)
        cases.append(bad_status)
        for evidence in cases:
            with self.subTest(observation=evidence):
                with self.assertRaises(ValueError):
                    auth.evaluate(intent(), evidence)

    def test_resume_rejects_non_state_and_non_resumable_state(self):
        with self.assertRaises(ValueError):
            auth.resume({}, observation())
        authorized = auth.evaluate(intent(), observation())
        with self.assertRaisesRegex(ValueError, "cannot be resumed"):
            auth.resume(authorized, observation())


if __name__ == "__main__":
    unittest.main()
