"""Protect the approved contributor grant and the surrounding license terms."""
import hashlib
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
GRANT = (
    "Each Authorized Contributor grants the copyright holder a perpetual, "
    "irrevocable, worldwide, royalty-free license to use, copy, modify, distribute "
    "and relicense their contributions to this project, under any terms. "
    "This grant survives the end of their authorization."
)
OWNERSHIP = "This license does not transfer ownership of contributions."
FOLLOWING = "Separate agreements and third-party licenses remain applicable"
# Approved development-permission text at e292787, with whitespace normalized.
BASELINE_SHA256 = "8bd29943ae81d47f9cd5581b8a4f60818cf03ce9bc7ddfffbfde5c7d86dedb15"


class ProprietaryTemplateTests(unittest.TestCase):
    def license_text(self):
        text = (ROOT / "skills/github-workflow/templates/LICENSE-proprietary.txt").read_text(encoding="utf-8")
        return " ".join(text.split())

    def test_exact_grant_occurs_once_after_ownership(self):
        text = self.license_text()
        self.assertEqual(text.count(GRANT), 1)
        self.assertIn(f"{OWNERSHIP} {GRANT} {FOLLOWING}", text)

    def test_other_terms_match_approved_baseline(self):
        without_grant = " ".join(self.license_text().replace(GRANT, "", 1).split())
        self.assertEqual(hashlib.sha256(without_grant.encode()).hexdigest(), BASELINE_SHA256)


if __name__ == "__main__":
    unittest.main()
