"""Exercise the documented exact license comparison in disposable repositories."""

from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
import unittest

from tests._fixture_support import fixture_environment, run


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "skills/github-workflow/references/adoption-and-licenses.md"
DOWNLOAD = re.compile(r"^( *)source = urlopen\(urls\[kind\], timeout=30\)\.read\(\)$", re.MULTILINE)
MIT_SOURCE = (
    "---\ntitle: MIT License\nspdx-id: MIT\n---\n\n"
    "MIT License\n\nCopyright (c) [year] [fullname]\n\nPermission is hereby granted.\n"
)
MIT_LICENSE = "MIT License\n\nCopyright (c) 2026 Example Org\n\nPermission is hereby granted.\n"


def documented_command() -> str:
    text = REFERENCE.read_text(encoding="utf-8")
    for match in re.finditer(r"```bash\n(.*?)\n```", text, re.DOTALL):
        if "Exact license text verified" in match.group(1):
            # The test reads a local copy instead of the official URL; nothing else changes.
            command, count = DOWNLOAD.subn(r'\1source = open(os.environ["LICENSE_FIXTURE"], "rb").read()', match.group(1))
            if count != 1:
                raise AssertionError("Expected exactly one official download line")
            return command
    raise AssertionError("No exact license comparison command found")


class LicenseCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory(prefix="license-check-fixture-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.environment, _ = fixture_environment(self.root)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "core.autocrlf", "false")
        self.source = self.root / "mit.txt"
        self.source.write_bytes(MIT_SOURCE.encode())
        self.environment |= {
            "LICENSE_KIND": "MIT",
            "LICENSE_PATH": "LICENSE",
            "LICENSE_YEAR": "2026",
            "LICENSE_HOLDER": "Example Org",
            "LICENSE_FIXTURE": str(self.source),
        }
        self.license = self.repo / "LICENSE"

    def git(self, *arguments: str):
        result = run(["git", *arguments], self.repo, self.environment)
        self.assertEqual(result.returncode, 0, f"git {arguments}: {result.stderr}")
        return result

    def commit(self, content: bytes) -> None:
        self.license.write_bytes(content)
        self.git("add", "LICENSE")
        self.git("commit", "-qm", "license")

    def check(self, expected: int, message: str, environment: dict[str, str] | None = None) -> None:
        # An absolute bash keeps the command runnable under a PATH without Python.
        bash = shutil.which("bash", path=self.environment["PATH"])
        self.assertIsNotNone(bash)
        result = run([bash, "-c", documented_command()], self.repo, environment or self.environment)
        self.assertEqual(result.returncode, expected, f"stdout={result.stdout!r}, stderr={result.stderr!r}")
        self.assertIn(message, result.stdout + result.stderr)

    def test_autocrlf_checkout_matches_staged_blob(self) -> None:
        self.commit(MIT_LICENSE.encode())
        self.git("config", "core.autocrlf", "true")
        self.license.unlink()
        self.git("checkout", "--", "LICENSE")
        self.assertIn(b"\r\n", self.license.read_bytes())
        self.check(0, "Exact license text verified: staged LICENSE")

    def test_modified_text_differs(self) -> None:
        self.commit(MIT_LICENSE.replace("hereby", "not").encode())
        self.check(1, "-Permission is hereby granted.")

    def test_committed_crlf_blob_differs(self) -> None:
        self.commit(MIT_LICENSE.replace("\n", "\r\n").encode())
        self.check(1, "The staged blob contains CRLF line endings")

    def test_untracked_license_is_not_compared(self) -> None:
        self.license.write_bytes(MIT_LICENSE.encode())
        self.check(2, "Stage the final LICENSE first; exact license comparison not run.")

    def test_unstaged_change_is_not_compared(self) -> None:
        self.commit(MIT_LICENSE.encode())
        self.license.write_bytes(MIT_LICENSE.replace("hereby", "not").encode())
        self.check(2, "Stage the final LICENSE first; exact license comparison not run.")

    def test_missing_holder_is_not_compared(self) -> None:
        self.commit(MIT_LICENSE.encode())
        self.check(2, "exact license comparison not run.", self.environment | {"LICENSE_HOLDER": " "})

    def test_unavailable_official_text_is_not_compared(self) -> None:
        self.commit(MIT_LICENSE.encode())
        self.source.unlink()
        self.check(2, "Official text unavailable")

    def test_missing_python_is_not_compared(self) -> None:
        self.commit(MIT_LICENSE.encode())
        tools = self.root / "git-only"
        tools.mkdir()
        git = shutil.which("git", path=self.environment["PATH"])
        self.assertIsNotNone(git)
        (tools / "git").symlink_to(git)
        self.check(2, "Python 3.8+ not found; exact license comparison not run.",
                   self.environment | {"PATH": str(tools)})


if __name__ == "__main__":
    unittest.main()
