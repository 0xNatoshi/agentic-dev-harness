"""Exercise the documented branch deletion command in disposable repositories."""

from pathlib import Path
import re
import shlex
import shutil
from tempfile import TemporaryDirectory
import unittest

from tests._fixture_support import fixture_environment, run


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "skills/github-workflow/references/workspace-lifecycle.md"
SCENARIOS = (
    "attached",
    "rebase_merge",
    "rebase_apply",
    "bisect",
    "unknown_state",
    "missing_worktree",
    "wrong_repository",
    "inventory_error",
    "other_rebase",
    "detached_rebase_merge",
    "detached_rebase_apply",
    "detached_rebase_update_refs",
    "other_rebase_update_refs",
    "other_am",
    "target_am",
    "incomplete_rebase_apply",
    "invalid_head_name",
    "update_refs_empty",
    "update_refs_truncated",
    "update_refs_invalid_oid",
    "update_refs_invalid_ref",
    "update_refs_nul",
    "am_marker_only",
    "free",
)
STATUS = {
    "unknown_state": 2,
    "missing_worktree": 2,
    "wrong_repository": 2,
    "inventory_error": 2,
    "incomplete_rebase_apply": 2,
    "invalid_head_name": 2,
    "update_refs_empty": 2,
    "update_refs_truncated": 2,
    "update_refs_invalid_oid": 2,
    "update_refs_invalid_ref": 2,
    "update_refs_nul": 2,
    "am_marker_only": 2,
    "free": 0,
    "other_rebase": 0,
    "detached_rebase_merge": 0,
    "detached_rebase_apply": 0,
    "other_am": 0,
}


def documented_command() -> str:
    text = REFERENCE.read_text(encoding="utf-8")
    for match in re.finditer(r"```bash\n(.*?)\n```", text, re.DOTALL):
        if 'git update-ref -d "refs/heads/$branch" "$head"' in match.group(1):
            return match.group(1)
    raise AssertionError("No guarded local branch deletion command found")


class BranchCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory(prefix="branch-cleanup-fixture-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.environment, self.tools = fixture_environment(self.root)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.repo / "file").write_text("base\n")
        self.git("add", "file")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        self.default = self.git("symbolic-ref", "--short", "HEAD").stdout.strip()
        self.git("branch", "target")

    def git(
        self, *arguments: str, directory: Path | None = None, check: bool = True
    ):
        result = run(["git", *arguments], directory or self.repo, self.environment)
        if check:
            self.assertEqual(result.returncode, 0, f"git {arguments}: {result.stderr}")
        return result

    def prepare(self, scenario: str) -> None:
        if scenario in ("free", "inventory_error"):
            return
        branch = "other" if scenario.startswith("other_") else "target"
        if branch == "other":
            self.git("branch", "other")
        worktree = self.root / "linked worktree"
        if scenario.endswith("update_refs"):
            # target sits inside the rebased range, so Git will rewrite it at the end.
            detach = ["--detach"] if scenario.startswith("detached") else []
            self.git("worktree", "add", "-q", *detach, str(worktree), "target" if detach else branch)
            (worktree / "extra").write_text("extra\n")
            self.git("add", "extra", directory=worktree)
            self.git("commit", "-qm", "extra", directory=worktree)
            self.git("branch", "-f", "target", "HEAD", directory=worktree)
            self.conflicting_commits(worktree)
            result = self.git("rebase", "--update-refs", self.default, directory=worktree, check=False)
            self.assertNotEqual(result.returncode, 0, "Fixture rebase unexpectedly succeeded")
            self.assertIn("refs/heads/target", (self.gitdir(worktree) / "rebase-merge" / "update-refs").read_text())
            return
        if scenario.startswith(("detached_rebase", "update_refs_")):
            self.git("worktree", "add", "-q", "--detach", str(worktree), branch)
            self.conflicting_commits(worktree)
            backend = "rebase-apply" if scenario == "detached_rebase_apply" else "rebase-merge"
            self.start_rebase(worktree, backend)
            self.assertEqual((self.gitdir(worktree) / backend / "head-name").read_text().strip(), "detached HEAD")
            # A lost or damaged update record must not read as "target is not listed".
            oid = self.base
            records = {
                "update_refs_empty": "",
                "update_refs_truncated": f"refs/heads/other\n{oid}\n",
                "update_refs_invalid_oid": "refs/heads/other\nnot-an-object-id\n" + "0" * 40 + "\n",
                "update_refs_invalid_ref": f"refs/heads/other bad\n{oid}\n{oid}\n",
                "update_refs_nul": f"refs/heads/target\0\n{oid}\n{oid}\n",
            }
            if scenario in records:
                (self.gitdir(worktree) / backend / "update-refs").write_text(records[scenario])
            return
        if scenario in (
            "unknown_state", "missing_worktree", "wrong_repository", "incomplete_rebase_apply", "invalid_head_name",
            "am_marker_only",
        ):
            self.git("worktree", "add", "-q", "--detach", str(worktree), branch)
            if scenario in ("unknown_state", "incomplete_rebase_apply", "invalid_head_name", "am_marker_only"):
                state = "rebase-apply" if scenario in ("incomplete_rebase_apply", "am_marker_only") else "rebase-merge"
                (self.gitdir(worktree) / state).mkdir()
                if scenario == "am_marker_only":
                    # A bare applying marker without am's patch counters is truncated state.
                    (self.gitdir(worktree) / state / "applying").write_text("")
                if scenario == "invalid_head_name":
                    # Only Git's exact literal is accepted; a near miss stays untrusted.
                    (self.gitdir(worktree) / state / "head-name").write_text("detached HEAD \n")
            else:
                worktree.rename(self.root / "moved worktree")
                if scenario == "missing_worktree":
                    self.assertIn("prunable", self.git("worktree", "list", "--porcelain").stdout)
                else:
                    worktree.mkdir()
                    self.git("init", "-q", "-b", "main", directory=worktree)
            return
        self.git("worktree", "add", "-q", str(worktree), branch)
        if scenario == "attached":
            return
        if scenario.endswith("_am"):
            self.conflicting_commits(worktree)
            patches = self.root / "patches"
            self.git("format-patch", "-q", "-1", "-o", str(patches), "HEAD")
            result = self.git("am", str(next(patches.iterdir())), directory=worktree, check=False)
            self.assertNotEqual(result.returncode, 0, "Fixture am unexpectedly succeeded")
            state = self.gitdir(worktree) / "rebase-apply"
            self.assertTrue((state / "applying").is_file())
            self.assertFalse((state / "head-name").exists())
            return
        if scenario == "bisect":
            for number in range(1, 6):
                (worktree / "file").write_text(f"{number}\n")
                self.git("commit", "-qam", f"change {number}", directory=worktree)
            self.git("bisect", "start", directory=worktree)
            self.git("bisect", "bad", "HEAD", directory=worktree)
            self.git("bisect", "good", self.base, directory=worktree)
            gitdir = Path(self.git("rev-parse", "--absolute-git-dir", directory=worktree).stdout.strip())
            self.assertEqual((gitdir / "BISECT_START").read_text().strip(), branch)
        else:
            self.conflicting_commits(worktree)
            state = "rebase-apply" if scenario == "rebase_apply" else "rebase-merge"
            self.start_rebase(worktree, state)
            self.assertEqual((self.gitdir(worktree) / state / "head-name").read_text().strip(), f"refs/heads/{branch}")
        self.assertIn("detached", self.git("worktree", "list", "--porcelain").stdout)

    def gitdir(self, worktree: Path) -> Path:
        return Path(self.git("rev-parse", "--absolute-git-dir", directory=worktree).stdout.strip())

    def conflicting_commits(self, worktree: Path) -> None:
        (worktree / "file").write_text("branch\n")
        self.git("commit", "-qam", "branch change", directory=worktree)
        (self.repo / "file").write_text("default\n")
        self.git("commit", "-qam", "default change")

    def start_rebase(self, worktree: Path, backend: str) -> None:
        arguments = ["rebase"]
        if backend == "rebase-apply":
            arguments.append("--apply")
        arguments.append(self.default)
        result = self.git(*arguments, directory=worktree, check=False)
        self.assertNotEqual(result.returncode, 0, "Fixture rebase unexpectedly succeeded")

    def check_scenario(self, scenario: str) -> None:
        self.prepare(scenario)
        head = self.git("rev-parse", "refs/heads/target").stdout.strip()
        environment = self.environment | {
            "branch": "target",
            "head": head,
            "skill_dir": str(REFERENCE.parent.parent),
        }
        if scenario == "inventory_error":
            real_git = shutil.which("git", path=environment["PATH"])
            self.assertIsNotNone(real_git)
            wrapper = self.tools / "git"
            wrapper.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = worktree ] && [ "$2" = list ]; then exit 77; fi\n'
                f"exec {shlex.quote(real_git)} \"$@\"\n"
            )
            wrapper.chmod(0o700)
        result = run(["bash", "-c", documented_command()], self.repo, environment)
        current = self.git("rev-parse", "--verify", "refs/heads/target", check=False)
        retained = current.returncode == 0 and current.stdout.strip() == head
        expected_status = STATUS.get(scenario, 1)
        should_retain = expected_status != 0
        self.assertEqual(
            (result.returncode, retained), (expected_status, should_retain),
            f"{scenario}: stdout={result.stdout!r}, stderr={result.stderr!r}",
        )


def make_test(scenario: str):
    def test(self: BranchCleanupTests) -> None:
        self.check_scenario(scenario)

    return test


for fixture in SCENARIOS:
    setattr(BranchCleanupTests, f"test_{fixture}", make_test(fixture))
