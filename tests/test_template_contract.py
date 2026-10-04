"""Exercise the distributed merge recipe and instruction-loading contract."""

import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

from tests._fixture_support import bash, fixture_environment, run, write_fixture

ROOT = Path(__file__).resolve().parents[1]
SKILL = Path("skills/github-workflow")
TEMPLATE = SKILL / "templates/AGENTS.md"
RECIPE_SOURCES = (Path("AGENTS.md"), TEMPLATE, SKILL / "SKILL.md")
SCAN_SOURCES = (
    *RECIPE_SOURCES,
    Path("profiles/AGENTS.template.md"),
    Path("profiles/hermes-development.md"),
)
VALIDATED_SHA = "a" * 40


def merge_recipe(path):
    blocks = re.findall(r"```bash\n(.*?)```", path.read_text(encoding="utf-8"), re.S)
    recipes = [block for block in blocks if "--match-head-commit" in block]
    if len(recipes) != 1:
        raise AssertionError(f"Expected one final merge recipe in {path}")
    recipe = recipes[0]
    for field, value in {
        "{{DEFAULT_BRANCH}}": "main", "{{MERGE_METHOD}}": "squash",
        "<default>": "main", "<method>": "squash", "<pr>": "1",
    }.items():
        recipe = recipe.replace(field, value)
    return recipe


class TemplateContractTests(unittest.TestCase):
    def test_adoption_fixture_honors_configured_bash_and_isolates_startup(self):
        with tempfile.TemporaryDirectory(prefix="configured-bash-") as directory:
            base = Path(directory)
            unexpected = base / "System32"
            unexpected.mkdir()
            launcher = unexpected / ("bash.exe" if os.name == "nt" else "bash")
            launcher.write_bytes(b"not an executable Bash fixture\n")
            launcher.chmod(0o700)
            marker = base / "startup-ran"
            startup = base / "ambient-startup"
            write_fixture(startup, f"printf poison > {shlex.quote(marker.as_posix())}\nexit 99\n")
            environment = os.environ.copy()
            environment.update({
                "PATH": str(unexpected) + os.pathsep + environment.get("PATH", os.defpath),
                "HARNESS_BASH": bash(),
                "BASH_ENV": str(startup),
                "PYTHONDONTWRITEBYTECODE": "1",
            })
            result = subprocess.run(
                [sys.executable, "-B", "-m", "unittest",
                 "tests.test_template_contract.TemplateContractTests.test_adoption_push_rechecks_origin_and_the_entrusted_branch"],
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(marker.exists(), "The fixture inherited an ambient Bash startup file")

    def test_adoption_push_rechecks_origin_and_the_entrusted_branch(self):
        text = (ROOT / SKILL / "SKILL.md").read_text(encoding="utf-8")
        fence = re.escape(chr(96) * 3)
        blocks = re.findall(fence + r"bash\n(.*?)" + fence, text, re.S)
        recipes = [block for block in blocks if "chore: formalise the git/github workflow" in block]
        self.assertEqual(len(recipes), 1)
        cases = (
            ("origin-failure", "chore/agent-workflow", "chore/agent-workflow", 2, False),
            ("host-change", "chore/agent-workflow", "chore/agent-workflow", 2, False),
            ("repo-change", "chore/agent-workflow", "chore/agent-workflow", 2, False),
            ("missing-claim", "chore/agent-workflow", "", 2, False),
            ("changed-checkout", "fix/other-task", "chore/agent-workflow", 2, False),
            ("fresh-default", "main", "main", 2, False),
            ("invalid-ref", "bad~ref", "bad~ref", 2, False),
            ("success", "chore/agent-workflow", "chore/agent-workflow", 0, True),
        )
        for failure, current, expected, status, pushed in cases:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(prefix="adoption push ") as directory:
                base = Path(directory)
                environment, _ = fixture_environment(base)
                guard = base / "skill/scripts/merge-preflight.sh"
                guard.parent.mkdir(parents=True)
                host = "other.example" if failure == "host-change" else "github.example"
                repo = "fixture/other" if failure == "repo-change" else "fixture/repository"
                write_fixture(guard,
                    "#!/usr/bin/env bash\n"
                    '[ "$1" = origin ] && [ "$2" = fixture/repository ] || exit 2\n'
                    + ("exit 2\n" if failure == "origin-failure" else
                       f"printf '%s\\t%s\\t%s\\n' {shlex.quote(host)} {shlex.quote(repo)} main\n"),
                )
                log = base / "push.log"
                setup = f"""
workflow_host=github.example
workflow_repo=fixture/repository
workflow_default=previous-default
workflow_expected_branch={shlex.quote(expected)}
workflow_test_branch={shlex.quote(current)}
workflow_test_failure={shlex.quote(failure)}
workflow_test_log={shlex.quote(log.as_posix())}
bash() {{ command {shlex.quote(Path(bash()).as_posix())} "$@"; }}
git() {{
  case "$1" in
    add|commit) return 0 ;;
    branch) printf '%s\\n' "$workflow_test_branch" ;;
    check-ref-format) [ "$workflow_test_failure" != invalid-ref ] ;;
    push) printf '%s\\n' "$*" >> "$workflow_test_log" ;;
    *) return 3 ;;
  esac
}}
gh() {{ printf '%s\\n' 'https://github.example/fixture/repository/pull/1'; }}
"""
                recipe = recipes[0].replace("<skill-dir>", shlex.quote((base / "skill").as_posix()))
                recipe = recipe.replace("<reviewed-files>", "README.md").replace("<file>", "body.md")
                result = run(["bash", "-c", setup + recipe], base, environment)
                self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                self.assertEqual(log.exists(), pushed, result.stdout + result.stderr)
                if pushed:
                    self.assertEqual(log.read_text().splitlines(),
                                     ['push -u origin HEAD:refs/heads/chore/agent-workflow'])

    def test_template_and_root_fit_instruction_loading_budget(self):
        # The source budget leaves room for project parameters; the root cap
        # comes from the native effective-default loading experiment in #15.
        for relative, budget in ((TEMPLATE, 29_515), (Path("AGENTS.md"), 32_768)):
            with self.subTest(source=str(relative)):
                self.assertLessEqual(len((ROOT / relative).read_bytes()), budget)

    def test_instruction_scan_inventory_is_identical(self):
        inventories = []
        for relative in SCAN_SOURCES:
            lines = [
                line for line in (ROOT / relative).read_text(encoding="utf-8").splitlines()
                if line.startswith("The suspension scan set is:")
            ]
            self.assertEqual(len(lines), 1, str(relative))
            inventories.append(lines[0])
        self.assertEqual(len(set(inventories)), 1, "Suspension scan inputs drifted")

    def test_merge_uses_only_the_successfully_validated_head(self):
        cases = (
            ("fetch", VALIDATED_SHA, VALIDATED_SHA, 2, False),
            ("merge-base", VALIDATED_SHA, VALIDATED_SHA, 2, False),
            ("view", VALIDATED_SHA, VALIDATED_SHA, 2, False),
            ("changed-head", VALIDATED_SHA, "b" * 40, 2, False),
            ("missing-head", None, VALIDATED_SHA, 2, False),
            ("malformed-head", "not-a-commit", "not-a-commit", 2, False),
            ("merge", VALIDATED_SHA, VALIDATED_SHA, 7, True),
            ("success", VALIDATED_SHA, VALIDATED_SHA, 0, True),
        )
        for relative in RECIPE_SOURCES:
            recipe = merge_recipe(ROOT / relative)
            for failure, validated, observed, exit_code, reaches_merge in cases:
                with self.subTest(source=str(relative), failure=failure):
                    setup = f"""
workflow_host=github.example
workflow_repo=fixture/repository
workflow_pr=https://github.example/fixture/repository/pull/1
fixture_failure={failure}
fixture_observed={observed}
function gh() {{
    case "$1 $2" in
        'pr view')
            printf '%s\\n' VIEW_ARGUMENTS "$@" END_VIEW_ARGUMENTS >&2
            [ "$fixture_failure" != view ] || return 1
            printf '%s\\n' "$fixture_observed"
            ;;
        'pr merge')
            printf '%s\\n' MERGE_REACHED "$@"
            [ "$fixture_failure" != merge ] || return 7
            ;;
        *) return 92 ;;
    esac
}}
function git() {{
    case "$1" in
        fetch)
            [ "$#" = 2 ] && [ "$2" = origin ] || return 93
            [ "$fixture_failure" != fetch ]
            ;;
        merge-base)
            [ "$#" = 4 ] && [ "$2" = --is-ancestor ] &&
                [ "$3" = origin/main ] && [ "$4" = {VALIDATED_SHA} ] || return 93
            [ "$fixture_failure" != merge-base ]
            ;;
        *) return 93 ;;
    esac
}}
"""
                    setup += (
                        "unset workflow_sha\n" if validated is None
                        else f"workflow_sha={validated}\n"
                    )
                    with tempfile.TemporaryDirectory(prefix="harness-merge-recipe-") as directory:
                        base = Path(directory)
                        environment, _ = fixture_environment(base)
                        result = run(["bash", "--noprofile", "--norc", "-c", setup + recipe],
                                     base, environment)
                    self.assertEqual(result.returncode, exit_code, result.stdout + result.stderr)
                    diagnostics = result.stderr.splitlines()
                    if "VIEW_ARGUMENTS" in diagnostics:
                        view_args = diagnostics[
                            diagnostics.index("VIEW_ARGUMENTS") + 1:diagnostics.index("END_VIEW_ARGUMENTS")
                        ]
                        self.assertEqual(view_args[view_args.index("--repo") + 1], "github.example/fixture/repository")
                        self.assertEqual(view_args[view_args.index("--json") + 1], "headRefOid")
                        self.assertEqual(view_args[view_args.index("--jq") + 1], ".headRefOid")
                        selectors = {"1", "https://github.example/fixture/repository/pull/1"}
                        self.assertEqual(len(selectors.intersection(view_args)), 1)
                    arguments = result.stdout.splitlines()
                    self.assertEqual("MERGE_REACHED" in arguments, reaches_merge, result.stdout)
                    if reaches_merge:
                        self.assertEqual(arguments.count("MERGE_REACHED"), 1)
                        self.assertEqual(arguments[1:3], ["pr", "merge"])
                        self.assertEqual(arguments[arguments.index("--repo") + 1], "github.example/fixture/repository")
                        self.assertEqual(arguments[arguments.index("--match-head-commit") + 1], VALIDATED_SHA)
                        selectors = {"1", "https://github.example/fixture/repository/pull/1"}
                        self.assertEqual(len(selectors.intersection(arguments)), 1)
                        self.assertIn("--squash", arguments)
                        self.assertNotIn("--admin", arguments)
                        self.assertNotIn("--auto", arguments)


if __name__ == "__main__":
    unittest.main()
