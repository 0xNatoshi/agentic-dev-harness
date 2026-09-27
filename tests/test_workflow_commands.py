"""Check the workflow command contract in disposable Markdown guidance."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_workflow_commands.py"
sys.dont_write_bytecode = True
sys.path.insert(0, str(SCRIPT.parent))
from check_workflow_commands import check_repository  # noqa: E402


PROBE = """```bash
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; assert sys.version_info >= (3,8)' 2>/dev/null; then
  python_cmd=(python3)
elif command -v py >/dev/null 2>&1 && py -3 -c 'import sys; assert sys.version_info >= (3,8)' 2>/dev/null; then
  python_cmd=(py -3)
elif command -v python >/dev/null 2>&1 && python -c 'import sys; assert sys.version_info >= (3,8)' 2>/dev/null; then
  python_cmd=(python)
else
  printf '%s\\n' 'Python unavailable' >&2
  exit 2
fi
```
"""
ORIGIN_PARAGRAPH = (
    "With multiple remotes gh may default to upstream. In the relevant checkout, run the following check. "
    "It compares origin fetch/push URLs, implicit `gh repo view --json nameWithOwner,url,defaultBranchRef` "
    "and an explicit repository read. A mismatch exits 2.\n"
)
SKILL = (
    "# GitHub development workflow\n\n**Tools**: Bash and Python.\n\n"
    + PROBE
    + "\n## Bind GitHub operations to origin\n\n"
    + ORIGIN_PARAGRAPH
    + "\n```bash\n"
    + 'workflow_pr=$(gh pr create --repo "$workflow_host/$workflow_repo" --draft --base "$workflow_default" --title "fix: example" --body-file <file>) || exit 2\n'
    + 'gh pr view --repo="$workflow_host/$workflow_repo" "$workflow_pr" --json body\n'
    + 'gh pr checks "--repo" "$workflow_host/$workflow_repo" "$workflow_pr" --watch\n'
    + 'gh run view --repo "$workflow_host/$workflow_repo" "$workflow_run" --log-failed\n'
    + 'gh issue view <n> --repo "$workflow_host/$workflow_repo"\n'
    + 'gh repo view "$workflow_host/$workflow_repo" --json nameWithOwner\n'
    + 'gh pr merge --repo "$workflow_host/$workflow_repo" "$workflow_pr" --squash --match-head-commit="$workflow_sha"\n'
    + 'gh pr view --repo "${workflow_host}/${workflow_repo}" 2>/dev/null "$workflow_pr" --json body\n'
    + '"${python_cmd[@]}" -c "print(1)"\n'
    + "```\n\n"
    + "The CLI names `gh pr view|checks|ready|merge|edit`, `gh repo view/edit`, and `gh issue list/view/create/comment` here.\n"
)


class WorkflowCommandTests(unittest.TestCase):
    def root(self, directory, skill=SKILL, references=None):
        root = Path(directory)
        guide = root / "skills/github-workflow"
        reference_dir = guide / "references"
        reference_dir.mkdir(parents=True)
        (guide / "SKILL.md").write_text(skill, encoding="utf-8")
        for name, contents in (references or {}).items():
            (reference_dir / name).write_text(contents, encoding="utf-8")
        return root

    def test_current_command_shapes_and_shorthand_are_accepted(self):
        with tempfile.TemporaryDirectory(prefix="workflow-commands-") as directory:
            root = self.root(directory, references={
                "readme-guide.md": 'Use `gh repo edit "$workflow_host/$workflow_repo" --description "$ABOUT_DESCRIPTION"`.\n',
            })
            self.assertEqual(check_repository(root), [])

    def test_each_banned_form_is_rejected_at_the_inserted_line(self):
        bad_examples = [
            ('gh pr view --repo "$workflow_host/$workflow_repo" --json body', "PR selector"),
            ('gh pr checks --repo "$workflow_host/$workflow_repo" --watch', "PR selector"),
            ('gh pr ready --repo "$workflow_host/$workflow_repo"', "PR selector"),
            ('gh pr merge --repo "$workflow_host/$workflow_repo" --squash', "PR selector"),
            ('gh pr edit --repo "$workflow_host/$workflow_repo" --title fix', "PR selector"),
            ('gh run view --repo "$workflow_host/$workflow_repo" --log-failed', "run ID"),
            ('gh run watch --repo "$workflow_host/$workflow_repo"', "run ID"),
            ('gh run rerun --repo "$workflow_host/$workflow_repo" --failed', "run ID"),
            ('gh run cancel --repo "$workflow_host/$workflow_repo"', "run ID"),
            ('gh pr view "$workflow_pr" --json body', "valued --repo"),
            ('gh pr view -R owner/repo "$workflow_pr"', "valued --repo"),
            ('gh run list --commit "$workflow_sha"', "valued --repo"),
            ('gh issue view <n>', "valued --repo"),
            ('gh issue develop <n> --checkout', "valued --repo"),
            ('gh issue create', "valued --repo"),
            ('gh repo view --json nameWithOwner', "explicit repository"),
            ('gh repo edit --description text', "explicit repository"),
            ('gh repo edit --enable-squash-merge --squash-merge-commit-message pr-title', "explicit repository"),
            ('gh pr merge --repo "$workflow_host/$workflow_repo" "$workflow_pr" --match-head-commit', "commit value"),
            ('gh pr merge --repo "$workflow_host/$workflow_repo" "$workflow_pr" --match-head-commit=', "commit value"),
            ('gh pr merge --repo "$workflow_host/$workflow_repo" "$workflow_pr" --match-head-commit ""', "commit value"),
            ('gh pr merge --repo "$workflow_host/$workflow_repo" "$workflow_pr" --match-head-commit " "', "commit value"),
            ('gh pr merge --repo "$workflow_host/$workflow_repo" "$workflow_pr" --match-head-commit="  "', "commit value"),
            ('gh pr merge --repo "$workflow_host/$workflow_repo" "$workflow_pr" --match-head-commit --squash', "commit value"),
            ("python3 -c 'print(1)'", "direct python3"),
            ("env python3 -c 'print(1)'", "direct python3"),
            ("python3 - <<'PY'", "direct python3"),
            ("python3 scripts/check.py", "direct python3"),
        ]
        for command, expected in bad_examples:
            with self.subTest(command=command), tempfile.TemporaryDirectory(prefix="workflow-mutation-") as directory:
                root = self.root(directory, references={"examples.md": "# Examples\n\n```bash\n" + command + "\n```\n"})
                findings = check_repository(root)
                self.assertTrue(any(
                    item.file == "skills/github-workflow/references/examples.md"
                    and item.line == 4 and expected in item.message
                    for item in findings
                ), [str(item) for item in findings])

    def test_option_values_do_not_count_as_selectors_or_repo_binding(self):
        bad = [
            ('gh pr view --repo "$workflow_host/$workflow_repo" --json body', "PR selector"),
            ('gh pr view --repo "$workflow_host/$workflow_repo" --jq ".body"', "PR selector"),
            ('gh pr view --repo --json body', "valued --repo"),
            ('gh pr view --repo "" "$workflow_pr"', "valued --repo"),
            ('gh pr view "--repo" --json body', "valued --repo"),
            ('gh run view --repo="$workflow_host/$workflow_repo" --job 123', "run ID"),
        ]
        for command, expected in bad:
            with self.subTest(command=command), tempfile.TemporaryDirectory(prefix="workflow-options-") as directory:
                root = self.root(directory, references={"examples.md": "`" + command + "`\n"})
                self.assertTrue(any(expected in item.message for item in check_repository(root)))

    def test_all_targeted_pr_and_run_commands_require_a_selector(self):
        commands = {
            "pr": ("checkout", "checks", "close", "comment", "diff", "edit", "lock", "merge",
                   "ready", "reopen", "revert", "review", "unlock", "update-branch", "view"),
            "run": ("cancel", "delete", "download", "rerun", "view", "watch"),
        }
        for family, subcommands in commands.items():
            for subcommand in subcommands:
                with self.subTest(family=family, subcommand=subcommand), tempfile.TemporaryDirectory(prefix="workflow-targets-") as directory:
                    command = f'gh {family} {subcommand} --repo "$workflow_host/$workflow_repo"'
                    root = self.root(directory, references={"examples.md": f"```bash\n{command}\n```\n"})
                    result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, encoding="utf-8", check=False)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("skills/github-workflow/references/examples.md:2:", result.stderr)
                    self.assertIn("PR selector" if family == "pr" else "run ID", result.stderr)
                    (root / "skills/github-workflow/references/examples.md").write_text(f"```bash\n{command} 25\n```\n", encoding="utf-8")
                    result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, encoding="utf-8", check=False)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_review_and_download_options_never_supply_a_selector(self):
        cases = (
            ('pr review --approve', "PR selector"),
            ('pr review --request-changes --body "needs a test"', "PR selector"),
            ('run download --dir artifacts --name build --pattern "*.zip"', "run ID"),
        )
        for tail, expected in cases:
            with self.subTest(command=tail), tempfile.TemporaryDirectory(prefix="workflow-target-options-") as directory:
                family, subcommand, options = tail.split(" ", 2)
                command = f'gh {family} {subcommand} --repo "$workflow_host/$workflow_repo" {options}'
                root = self.root(directory, references={"examples.md": f"`{command}`\n"})
                result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, encoding="utf-8", check=False)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("skills/github-workflow/references/examples.md:1:", result.stderr)
                self.assertIn(expected, result.stderr)
                (root / "skills/github-workflow/references/examples.md").write_text(f"`{command} 25`\n", encoding="utf-8")
                result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, encoding="utf-8", check=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_python_module_mode_requires_the_selected_interpreter(self):
        commands = (
            "python3 -m unittest", "python3 -munittest", "python3 -B -m unittest",
            "python3 -u -I -X dev -m unittest", "python3 2>/dev/null -m unittest",
        )
        for command in commands:
            with self.subTest(command=command), tempfile.TemporaryDirectory(prefix="workflow-python-module-") as directory:
                root = self.root(directory, references={"examples.md": f"```bash\n{command}\n```\n"})
                result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, encoding="utf-8", check=False)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("skills/github-workflow/references/examples.md:2:", result.stderr)
                self.assertIn("direct python3 invocation", result.stderr)
                selected = command.replace("python3", '"${python_cmd[@]}"', 1)
                (root / "skills/github-workflow/references/examples.md").write_text(f"```bash\n{selected}\n```\n", encoding="utf-8")
                result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, encoding="utf-8", check=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_options_are_specific_to_the_gh_subcommand(self):
        invalid = (
            'pr view --repo "$workflow_host/$workflow_repo" 25 --head main',
            'pr view --repo "$workflow_host/$workflow_repo" --approve 25',
            'run watch --repo "$workflow_host/$workflow_repo" --pattern "*.zip" 25',
            'issue view --repo "$workflow_host/$workflow_repo" 25 --checkout',
            'repo view "$workflow_host/$workflow_repo" --description text',
            'repo edit "$workflow_host/$workflow_repo" --json description',
            'pr merge --repo "$workflow_host/$workflow_repo" 25 --title text',
        )
        for tail in invalid:
            with self.subTest(command=tail), tempfile.TemporaryDirectory(prefix="workflow-wrong-option-") as directory:
                root = self.root(directory, references={"examples.md": f"`gh {tail}`\n"})
                result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, encoding="utf-8", check=False)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("skills/github-workflow/references/examples.md:1:", result.stderr)
                self.assertIn("unsupported gh option", result.stderr)
        valid = (
            'pr list --repo "$workflow_host/$workflow_repo" --head main',
            'pr status --repo "$workflow_host/$workflow_repo" --json createdBy',
            'run list --repo "$workflow_host/$workflow_repo" --branch main',
            'pr review --repo "$workflow_host/$workflow_repo" --comment 25',
            'pr lock --repo "$workflow_host/$workflow_repo" --reason resolved 25',
            'pr close --repo "$workflow_host/$workflow_repo" --comment done 25',
            'run download --repo "$workflow_host/$workflow_repo" -D artifacts 25',
            'repo edit "$workflow_host/$workflow_repo" --description text --template',
        )
        for tail in valid:
            with self.subTest(command=tail), tempfile.TemporaryDirectory(prefix="workflow-valid-option-") as directory:
                root = self.root(directory, references={"examples.md": f"`gh {tail}`\n"})
                result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, encoding="utf-8", check=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unknown_subcommands_fail_closed(self):
        cases = (
            ('pr future-command --repo "$workflow_host/$workflow_repo" 25', "unsupported gh command"),
            ('repo future-command "$workflow_host/$workflow_repo"', "unsupported gh command"),
        )
        for tail, expected in cases:
            with self.subTest(command=tail), tempfile.TemporaryDirectory(prefix="workflow-option-contract-") as directory:
                root = self.root(directory, references={"examples.md": f"`gh {tail}`\n"})
                result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, encoding="utf-8", check=False)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("skills/github-workflow/references/examples.md:1:", result.stderr)
                self.assertIn(expected, result.stderr)

    def test_surplus_positionals_respect_command_specific_limits(self):
        cases = (
            ("pr create", "extra", 0), ("pr list", "extra", 0), ("pr status", "extra", 0),
            ("pr view", "25 extra", 1), ("pr review", "25 extra --body text", 1),
            ("run list", "extra", 0), ("run view", "25 extra", 1),
            ("issue create", "extra", 0), ("issue list", "extra", 0), ("issue status", "extra", 0),
            ("issue transfer", "25 owner/destination extra", 2),
            ("repo view", "extra", 1), ("repo edit", "extra", 1), ("repo set-default", "extra", 1),
        )
        for command, operands, maximum in cases:
            with self.subTest(command=command), tempfile.TemporaryDirectory(prefix="workflow-positional-limit-") as directory:
                binding = '"$workflow_host/$workflow_repo"' if command.startswith("repo ") else '--repo "$workflow_host/$workflow_repo"'
                root = self.root(directory, references={"examples.md": f"`gh {command} {binding} {operands}`\n"})
                result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, encoding="utf-8", check=False)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(f"{command} accepts at most {maximum} positional operands", result.stderr)
        with tempfile.TemporaryDirectory(prefix="workflow-positional-forms-") as directory:
            root = self.root(directory, references={"examples.md": '''```bash
gh pr list --repo "$workflow_host/$workflow_repo" --head main --base main
gh pr review --repo "$workflow_host/$workflow_repo" 25 --body "two words"
gh issue edit --repo "$workflow_host/$workflow_repo" 25 26 27 --add-label triaged
gh issue transfer --repo "$workflow_host/$workflow_repo" 25 owner/destination
gh repo view "$workflow_host/$workflow_repo" --json nameWithOwner
```\n'''})
            self.assertEqual(check_repository(root), [])

    def test_interpreter_only_shell_commands_require_the_selected_array(self):
        for launcher in ("python3", "python", "py", "py -3"):
            for suffix in ("", " -B", " -X dev", " -W ignore", " -u -I"):
                for layout in ("```bash\n%s\n```\n", "%s\n"):
                    with self.subTest(launcher=launcher, suffix=suffix, layout=layout), tempfile.TemporaryDirectory(prefix="workflow-interactive-") as directory:
                        command = launcher + suffix
                        root = self.root(directory, references={"examples.md": layout % command})
                        findings = check_repository(root)
                        self.assertTrue(any(f"direct {launcher.split()[0]} invocation" in item.message for item in findings), [str(item) for item in findings])
                        (root / "skills/github-workflow/references/examples.md").write_text(layout % ('"${python_cmd[@]}"' + suffix), encoding="utf-8")
                        self.assertEqual(check_repository(root), [])

    def test_interpreter_metadata_and_array_literals_do_not_execute_python(self):
        examples = '''The snippet probes `python3`, `py -3` and `python`.
```bash
python_cmd=(python3)
python_cmd=(py -3)
python_cmd=(env python3 -u)
python_cmd+=(python3)
python_cmd+=(py -3)
printf '%s' python3
```
'''
        with tempfile.TemporaryDirectory(prefix="workflow-python-metadata-") as directory:
            root = self.root(directory, references={"examples.md": examples})
            reference = root / "skills/github-workflow/references/examples.md"
            self.assertEqual(check_repository(root), [])
            for launcher in ("python3", "python", "py", "py -3"):
                for flag in ("--version", "-V", "-VV", "--help", "-h"):
                    with self.subTest(launcher=launcher, flag=flag):
                        reference.write_text(f"```bash\n{launcher} {flag}\n```\n", encoding="utf-8")
                        self.assertEqual(check_repository(root), [])
            for command in ("python3 -X", "python3 -W", "py -3 -X"):
                reference.write_text(f"```bash\n{command}\n```\n", encoding="utf-8")
                self.assertTrue(any("unsupported" in item.message for item in check_repository(root)))
            for command in ('python_cmd=($(python3))', 'python_cmd=($(py -3))', 'python_cmd+=($(python3))', 'python_cmd+=($(py -3))'):
                reference.write_text(f"```bash\n{command}\n```\n", encoding="utf-8")
                self.assertTrue(any("direct" in item.message for item in check_repository(root)))
            for assignment in ("args=(25)", "args+=(25)"):
                for separator in (";", "&&", "||", "|"):
                    for prefix in ("", "env ", "command "):
                        for command, expected in (
                            ("python -m unittest", "direct python invocation"),
                            ('gh pr view --repo "$workflow_host/$workflow_repo"', "explicit PR selector"),
                        ):
                            with self.subTest(assignment=assignment, separator=separator, prefix=prefix, command=command):
                                reference.write_text(f"```bash\n{assignment}{separator} {prefix}{command}\n```\n", encoding="utf-8")
                                self.assertTrue(any(expected in item.message for item in check_repository(root)))

    def test_required_issue_operands_are_not_supplied_by_option_values(self):
        commands = ("close", "comment", "delete", "develop", "edit", "lock", "reopen", "transfer", "unlock", "view")
        for subcommand in commands:
            with self.subTest(command=subcommand), tempfile.TemporaryDirectory(prefix="workflow-issue-minimum-") as directory:
                command = f'gh issue {subcommand} --repo "$workflow_host/$workflow_repo"'
                if subcommand == "edit":
                    command += ' --title "two words"'
                root = self.root(directory, references={"examples.md": f"```bash\n{command}\n```\n"})
                target = root / "skills/github-workflow/references/examples.md"
                minimum = 2 if subcommand == "transfer" else 1
                for operands in ("", " 25") if minimum == 2 else ("",):
                    with self.subTest(operands=operands):
                        target.write_text(f"```bash\n{command}{operands}\n```\n", encoding="utf-8")
                        self.assertTrue(any(f"needs at least {minimum} positional operands" in item.message for item in check_repository(root)))
                operands = " 25 owner/destination" if minimum == 2 else " 25"
                target.write_text(f"```bash\n{command}{operands}\n```\n", encoding="utf-8")
                self.assertEqual(check_repository(root), [])

    def test_default_repository_repair_is_bound_and_view_is_read_only(self):
        with tempfile.TemporaryDirectory(prefix="workflow-default-repository-") as directory:
            root = self.root(directory)
            target = root / "skills/github-workflow/references/examples.md"
            invalid = (
                ("", "explicit repository"),
                ("evil/repo", "verified origin"),
                ("origin", "verified origin"),
                ('"$workflow_repo"', "verified origin"),
                ('""', "verified origin"),
                ("-- --view", "verified origin"),
                ("--view evil/repo", "at most 0"),
                ('--view "$workflow_host/$workflow_repo"', "at most 0"),
                ("--unset", "outside the verified-origin repair"),
                ("-u", "outside the verified-origin repair"),
                ("--view --unset", "outside the verified-origin repair"),
            )
            for tail, expected in invalid:
                with self.subTest(tail=tail):
                    target.write_text(f"```bash\ngh repo set-default {tail}\n```\n", encoding="utf-8")
                    self.assertTrue(any(expected in item.message for item in check_repository(root)))
            for tail in ('"$workflow_host/$workflow_repo"', '"${workflow_host}/${workflow_repo}"', "--view", "-v"):
                with self.subTest(tail=tail):
                    target.write_text(f"```bash\ngh repo set-default {tail}\n```\n", encoding="utf-8")
                    self.assertEqual(check_repository(root), [])

    def test_command_family_shorthand_is_only_inline_metadata(self):
        for command in (
            'gh pr view/edit --repo "$workflow_host/$workflow_repo"',
            'gh pr view|edit --repo "$workflow_host/$workflow_repo"',
            'gh issue view/comment --repo "$workflow_host/$workflow_repo"',
            'gh repo view/edit',
        ):
            with tempfile.TemporaryDirectory(prefix="workflow-executable-shorthand-") as directory:
                root = self.root(directory, references={"examples.md": f"The CLI names `{command}`.\n"})
                target = root / "skills/github-workflow/references/examples.md"
                self.assertEqual(check_repository(root), [])
                for layout in ("```bash\n%s\n```\n", "%s\n"):
                    with self.subTest(command=command, layout=layout):
                        target.write_text(layout % command, encoding="utf-8")
                        self.assertTrue(check_repository(root))

    def test_required_option_values_are_reported_for_each_command(self):
        cases = (
            ('pr list --repo "$workflow_host/$workflow_repo" --head', "--head"),
            ('pr list --repo "$workflow_host/$workflow_repo" --head=', "--head"),
            ('pr list --repo "$workflow_host/$workflow_repo" --head=</dev/null', "--head"),
            ('pr list --repo "$workflow_host/$workflow_repo" --head=</dev/null>/dev/null', "--head"),
            ('pr list --repo "$workflow_host/$workflow_repo" --head=>output', "--head"),
            ('pr list --repo "$workflow_host/$workflow_repo" --head " "', "--head"),
            ('pr list --repo "$workflow_host/$workflow_repo" --head --base main', "--head"),
            ('pr view --repo "$workflow_host/$workflow_repo" 25 --json', "--json"),
            ('run list --repo "$workflow_host/$workflow_repo" --branch', "--branch"),
            ('issue create --repo "$workflow_host/$workflow_repo" --title', "--title"),
            ('repo edit "$workflow_host/$workflow_repo" --description', "--description"),
            ('pr review --repo "$workflow_host/$workflow_repo" 25 --body-file', "--body-file"),
        )
        for tail, flag in cases:
            with self.subTest(command=tail), tempfile.TemporaryDirectory(prefix="workflow-missing-value-") as directory:
                root = self.root(directory, references={"examples.md": f"`gh {tail}`\n"})
                result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, encoding="utf-8", check=False)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("skills/github-workflow/references/examples.md:1:", result.stderr)
                self.assertIn(f"{flag} needs a value", result.stderr)

    def test_value_diagnostics_preserve_documentation_operands_and_stdin(self):
        with tempfile.TemporaryDirectory(prefix="workflow-value-operands-") as directory:
            root = self.root(directory, references={"examples.md": '''```bash
gh pr create --repo "$workflow_host/$workflow_repo" --title "<type>(scope): summary" --body-file <file>
gh issue develop <n> --repo "$workflow_host/$workflow_repo" --name <type>/<n>-<slug> --base <default> --checkout
gh pr review --repo "$workflow_host/$workflow_repo" 25 --body-file -
gh pr comment --repo "$workflow_host/$workflow_repo" 25 --body-file=-
gh pr list --repo "$workflow_host/$workflow_repo" --head=2>/dev/null
gh pr create --repo "$workflow_host/$workflow_repo" --title="<type>(scope): summary" --body-file=<file>
```\n'''})
            self.assertEqual(check_repository(root), [])
            reference = root / "skills/github-workflow/references/examples.md"
            reference.write_text('```bash\ngh pr list --repo "$workflow_host/$workflow_repo" --head --base\n```\n', encoding="utf-8")
            findings = check_repository(root)
            self.assertEqual(len(findings), 2, [str(item) for item in findings])
            self.assertTrue(any("--head needs a value" in item.message for item in findings))
            self.assertTrue(any("--base needs a value" in item.message for item in findings))
            reference.write_text('```bash\ngh pr view --repo "$workflow_host/$workflow_repo" <input>output\n```\n', encoding="utf-8")
            self.assertTrue(any("PR selector" in item.message for item in check_repository(root)))

    def test_fallback_launchers_need_the_selected_interpreter(self):
        for launcher in ("python3", "python", "py", "py -3"):
            for arguments in ("scripts/check.py", "scripts/check", "/dev/null", "app.zip", "app_dir", "-m unittest", "-c 'print(1)'", "-"):
                for layout in ("```bash\n%s\n```\n", "`%s`\n", "%s\n"):
                    command = f"{launcher} {arguments}"
                    with self.subTest(command=command, layout=layout), tempfile.TemporaryDirectory(prefix="workflow-fallback-") as directory:
                        root = self.root(directory, references={"examples.md": layout % command})
                        result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, encoding="utf-8", check=False)
                        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                        self.assertIn("skills/github-workflow/references/examples.md:", result.stderr)
                        self.assertIn(f"direct {launcher.split()[0]} invocation", result.stderr)
                        selected = '"${python_cmd[@]}" ' + arguments
                        (root / "skills/github-workflow/references/examples.md").write_text(layout % selected, encoding="utf-8")
                        result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, encoding="utf-8", check=False)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_compound_redirects_never_supply_a_selector_or_option_value(self):
        for redirect in ("</dev/null>/dev/null", "<input>/dev/null", "<input>./output", "<input>-output"):
            for command, expected in (("pr view", "PR selector"), ("run download", "run ID"), ("pr list", "--head needs a value")):
                with self.subTest(redirect=redirect, command=command), tempfile.TemporaryDirectory(prefix="workflow-compound-redirect-") as directory:
                    option = " --head" if command == "pr list" else ""
                    example = f'gh {command} --repo "$workflow_host/$workflow_repo"{option} {redirect}'
                    root = self.root(directory, references={"examples.md": f"`{example}`\n"})
                    result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, encoding="utf-8", check=False)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("skills/github-workflow/references/examples.md:1:", result.stderr)
                    self.assertIn(expected, result.stderr)

    def test_repeated_repo_options_keep_missing_values_visible(self):
        for missing in ("--repo", "--repo=", '--repo ""', '--repo " "'):
            for order in ('{origin} {missing}', '{missing} {origin}'):
                with self.subTest(missing=missing, order=order), tempfile.TemporaryDirectory(prefix="workflow-repeated-repo-") as directory:
                    options = order.format(origin='--repo "$workflow_host/$workflow_repo"', missing=missing)
                    root = self.root(directory, references={"examples.md": f"`gh pr view 25 {options}`\n"})
                    result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, encoding="utf-8", check=False)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("needs a valued --repo", result.stderr)

    def test_fallback_probes_require_the_complete_three_way_block(self):
        invalid = (
            "```bash\nelif command -v py >/dev/null 2>&1 && py -3 -c 'import sys; assert sys.version_info >= (3,8)' 2>/dev/null; then\n```\n",
            "```bash\nelif command -v python >/dev/null 2>&1 && python -c 'import sys; assert sys.version_info >= (3,8)' 2>/dev/null; then\n```\n",
            PROBE.replace("python_cmd=(py -3)", "python_cmd=(python)"),
        )
        for example in invalid:
            with self.subTest(example=example), tempfile.TemporaryDirectory(prefix="workflow-fallback-probe-") as directory:
                root = self.root(directory, references={"examples.md": PROBE})
                self.assertEqual(check_repository(root), [])
                (root / "skills/github-workflow/references/examples.md").write_text(example, encoding="utf-8")
                findings = check_repository(root)
                expected = "direct py invocation" if "py -3 -c" in example else "direct python invocation"
                self.assertTrue(any(expected in item.message for item in findings), [str(item) for item in findings])

    def test_reviewed_false_greens_fail_the_standalone_cli(self):
        cases = [
            ('gh pr view --repo "$workflow_host/$workflow_repo" --json body 2>/dev/null', "PR selector"),
            ('gh pr view --repo "$workflow_host/$workflow_repo" --json body 2> error.log', "PR selector"),
            ('gh run view --repo "$workflow_host/$workflow_repo" --log-failed 2>&1', "run ID"),
            ('gh run watch --repo "$workflow_host/$workflow_repo" 2> error.log', "run ID"),
            ('gh pr edit --repo "$workflow_host/$workflow_repo" --add-label bug', "PR selector"),
            ('gh pr edit --repo "$workflow_host/$workflow_repo" --add-reviewer reviewer', "PR selector"),
            ('gh pr edit --repo "$workflow_host/$workflow_repo" --new-flag maybe-value', "unsupported gh option"),
            ('python3 -B scripts/check.py', "direct python3"),
            ('python3 -u -c "print(1)"', "direct python3"),
            ('python3 -I -c "print(1)"', "direct python3"),
            ('python3 -X dev -c "print(1)"', "direct python3"),
            ('gh pr view --repo other/repo <pr> --json body', "verified origin"),
            ('gh issue view <n> --repo other/repo', "verified origin"),
            ('gh repo edit other/repo --description text', "explicit repository"),
            ('gh repo edit "$workflow_host/$workflow_repo" --squash-merge-commit-message pr-title', "unsupported gh option"),
        ]
        for command, expected in cases:
            with self.subTest(command=command), tempfile.TemporaryDirectory(prefix="workflow-reviewed-") as directory:
                root = self.root(directory, references={"examples.md": "# Examples\n\n```bash\n" + command + "\n```\n"})
                result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("skills/github-workflow/references/examples.md:4:", result.stderr)
                self.assertIn(expected, result.stderr)

    def test_redirection_operators_never_supply_a_selector_or_run_id(self):
        redirections = [
            "2>/dev/null", "2> error.log", "2>&1", "&>/dev/null",
            "&> /dev/null", "&>>logs.txt", "&>> logs.txt",
            "<<<input", "<<< input", "<<-EOF", "<>scratch", ">|output",
        ]
        for redirect in redirections:
            for family, subcommand, expected, selector in [
                ("pr", "view", "PR selector", "<pr>"),
                ("run", "view", "run ID", "<id>"),
            ]:
                with self.subTest(redirect=redirect, family=family), tempfile.TemporaryDirectory(prefix="workflow-redirect-") as directory:
                    prefix = f'gh {family} {subcommand} --repo "$workflow_host/$workflow_repo"'
                    bad = prefix + " " + redirect
                    good = prefix + " " + redirect + " " + selector
                    if redirect == "<<-EOF":
                        bad += "\npayload\nEOF"
                        good += "\npayload\nEOF"
                    root = self.root(directory, references={"examples.md": "# Examples\n\n```bash\n" + bad + "\n```\n"})
                    result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, check=False)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("skills/github-workflow/references/examples.md:4:", result.stderr)
                    self.assertIn(expected, result.stderr)
                    (root / "skills/github-workflow/references/examples.md").write_text("# Examples\n\n```bash\n" + good + "\n```\n", encoding="utf-8")
                    result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, check=False)
                    if redirect == "<<-EOF":
                        # The operand is valid, but unquoted bodies are outside
                        # the explicitly bounded here-document grammar.
                        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                        self.assertIn("unsupported here-document header", result.stderr)
                        self.assertNotIn(expected, result.stderr)
                    else:
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_adjacent_shell_controls_and_incomplete_redirect_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="workflow-controls-") as directory:
            examples = (
                'gh pr view --repo "$workflow_host/$workflow_repo" --json body&&'
                'gh pr checks --repo "$workflow_host/$workflow_repo" <pr>\n'
                'gh run view --repo "$workflow_host/$workflow_repo" --log-failed&&'
                'gh issue view <n> --repo "$workflow_host/$workflow_repo"\n'
                'gh pr edit --repo "$workflow_host/$workflow_repo" <pr> &>\n'
                'gh pr view --repo "$workflow_host/$workflow_repo" -- &>/dev/null\n'
            )
            root = self.root(directory, references={"examples.md": "```bash\n" + examples + "```\n"})
            findings = check_repository(root)
            self.assertTrue(any(item.line == 2 and "PR selector" in item.message for item in findings), [str(item) for item in findings])
            self.assertTrue(any(item.line == 3 and "run ID" in item.message for item in findings), [str(item) for item in findings])
            self.assertTrue(any(item.line == 4 and "incomplete shell redirection" in item.message for item in findings), [str(item) for item in findings])
            self.assertTrue(any(item.line == 5 and "PR selector" in item.message for item in findings), [str(item) for item in findings])

    def test_control_operators_never_supply_a_selector_or_run_id(self):
        for control in ("|", "|&", "&&", "||", ";", "&", ";;", ";&", ";;&"):
            for family, expected in (("pr", "PR selector"), ("run", "run ID")):
                with self.subTest(control=control, family=family), tempfile.TemporaryDirectory(prefix="workflow-pipeline-") as directory:
                    command = f'gh {family} view --repo "$workflow_host/$workflow_repo"'
                    if control in (";;", ";&", ";;&"):
                        bad = f"case fixture in fixture) {command}{control} esac"
                        good = f"case fixture in fixture) {command} 25{control} esac"
                    else:
                        bad = f"{command}{control} cat"
                        good = f"{command} 25{control} cat"
                    root = self.root(directory, references={"examples.md": "```bash\n" + bad + "\n```\n"})
                    findings = check_repository(root)
                    self.assertTrue(any("examples.md" in item.file and item.line == 2 and expected in item.message for item in findings), [str(item) for item in findings])
                    (root / "skills/github-workflow/references/examples.md").write_text("```bash\n" + good + "\n```\n", encoding="utf-8")
                    self.assertEqual(check_repository(root), [])

    def test_skeptical_operand_and_context_mutations(self):
        cases = (
            ('gh pr view --repo "$workflow_host/$workflow_repo" -R other/repo 25 --json url', "repository alias"),
            ('gh pr view -R other/repo --repo "$workflow_host/$workflow_repo" 25 --json url', "repository alias"),
            ('gh issue view --repo "$workflow_host/$workflow_repo" -R other/repo 25', "repository alias"),
            ('gh pr edit --repo "$workflow_host/$workflow_repo" --body "Use gh repo view/edit"', "PR selector"),
            ('gh repo view/edit && gh pr view --repo "$workflow_host/$workflow_repo"', "PR selector"),
            ('gh pr view --repo "$workflow_host/$workflow_repo" "" --json body', "empty gh operand"),
            ('gh run view --repo "$workflow_host/$workflow_repo" " " --log-failed', "empty gh operand"),
            ('python3 2>/dev/null scripts/check.py', "direct python3"),
            ('python3 2> errors.log -c "print(1)"', "direct python3"),
            ('python3 2>&1 -', "direct python3"),
            ('2>/dev/null gh pr view --repo "$workflow_host/$workflow_repo"', "PR selector"),
            ('2> errors.log python3 scripts/check.py', "direct python3"),
            ('elif gh pr view --repo "$workflow_host/$workflow_repo"; then', "PR selector"),
        )
        for command, expected in cases:
            with self.subTest(command=command), tempfile.TemporaryDirectory(prefix="workflow-skeptical-") as directory:
                root = self.root(directory, references={"examples.md": "`" + command + "`\n"})
                result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("skills/github-workflow/references/examples.md:1:", result.stderr)
                self.assertIn(expected, result.stderr)

    def test_bare_inline_command_is_distinct_from_slash_shorthand(self):
        with tempfile.TemporaryDirectory(prefix="workflow-inline-") as directory:
            root = self.root(directory, references={
                "examples.md": "Create one with `gh issue create`. The `gh issue list/view/create/comment` names describe a family.\n",
            })
            findings = check_repository(root)
            self.assertEqual(len(findings), 1, [str(item) for item in findings])
            self.assertEqual(findings[0].line, 1)
            self.assertIn("valued --repo", findings[0].message)

    def test_console_prompt_is_scanned(self):
        with tempfile.TemporaryDirectory(prefix="workflow-console-") as directory:
            root = self.root(directory, references={"examples.md": "```console\n$ gh issue view 1\n```\n"})
            findings = check_repository(root)
            self.assertEqual(len(findings), 1, [str(item) for item in findings])
            self.assertEqual(findings[0].line, 2)
            self.assertIn("valued --repo", findings[0].message)

    def test_only_the_origin_comparison_and_complete_interpreter_probe_are_exempt(self):
        shifted = "\n\n\n" + SKILL
        with tempfile.TemporaryDirectory(prefix="workflow-exceptions-") as directory:
            root = self.root(directory, skill=shifted)
            self.assertEqual(check_repository(root), [])
            extra = shifted.replace(
                ORIGIN_PARAGRAPH,
                ORIGIN_PARAGRAPH + "`gh repo view --json description`\n" + "```bash\ngh repo view --json url\n```\n",
            )
            extra = extra.replace("  python_cmd=(python3)\n", "  python_cmd=(python3)\n  python3 -c 'print(1)'\n")
            (root / "skills/github-workflow/SKILL.md").write_text(extra, encoding="utf-8")
            findings = check_repository(root)
            self.assertEqual(sum("explicit repository" in item.message for item in findings), 2, [str(item) for item in findings])
            self.assertEqual(sum("direct python3" in item.message for item in findings), 2, [str(item) for item in findings])

    def test_reference_subshell_probe_does_not_hide_other_python_calls(self):
        probe = PROBE.replace("```bash\n", "```bash\n(\n  ").replace(
            "fi\n```", "fi\n  \"${python_cmd[@]}\" - <<'PY'\nprint(1)\nPY\n)\n```",
        )
        with tempfile.TemporaryDirectory(prefix="workflow-reference-probe-") as directory:
            root = self.root(directory, references={
                "adoption-and-licenses.md": "### Compare standard texts\n\n" + probe,
            })
            self.assertEqual(check_repository(root), [])
            reference = root / "skills/github-workflow/references/adoption-and-licenses.md"
            reference.write_text(reference.read_text(encoding="utf-8").replace(
                "  \"${python_cmd[@]}\" - <<'PY'",
                "  python3 scripts/check.py\n  \"${python_cmd[@]}\" - <<'PY'",
            ), encoding="utf-8")
            findings = check_repository(root)
            self.assertEqual(len(findings), 1, [str(item) for item in findings])
            self.assertIn("direct python3", findings[0].message)

    def test_bash_continuations_and_command_substitutions(self):
        examples = '''```bash
workflow_pr=$(gh pr list \\
  --repo "$workflow_host/$workflow_repo" \\
  --state open)
gh pr view \\
  --repo "$workflow_host/$workflow_repo" \\
  "$workflow_pr" --json body
```
'''
        with tempfile.TemporaryDirectory(prefix="workflow-multiline-") as directory:
            root = self.root(directory, references={"examples.md": examples})
            self.assertEqual(check_repository(root), [])
            broken = examples.replace('  "$workflow_pr" --json body', '  --json body')
            (root / "skills/github-workflow/references/examples.md").write_text(broken, encoding="utf-8")
            findings = check_repository(root)
            self.assertTrue(any(item.line == 5 and "PR selector" in item.message for item in findings), [str(item) for item in findings])

    def test_execution_prefixes_and_groups_preserve_command_checks(self):
        wrappers = (
            "exec {}", "exec -cl {}", "exec -a name {}", "exec -aname {}",
            "exec -- {}", "command -p {}", "command -- {}", "env {}",
            "env -i NAME=value {}", "env -u GH_REPO {}", "env -uGH_REPO {}",
            "env --unset=GH_REPO {}", "env -C . {}", "env --chdir=. {}",
            "time -p {}", "env -i command -p {}", "NAME=value exec {}",
            "2>/dev/null exec 2>errors.log {}", "{{ {}; }}",
            "if true; then {{ {}; }}; fi", "case x in x) exec {}; esac",
            "case x in (x) {}; esac", "case x in y) true;; (x) {}; esac",
            "case x in (x|y) {};; esac", "case x in x|y) {};; esac",
            "time case x in (x) {};; esac", "time -p case x in (x) {};; esac",
            "time -p case x in x) {};; esac",
            "(case x in (x) {}; esac)", "(case x in x) {}; esac)",
            "echo $(case x in (x) {}; esac)", "echo $(case x in x) {}; esac)",
            "case x in $({})) true;; esac", "case x in ($({})) true;; esac",
            "args+=($(exec {}))", "args+=($(command -p {}))",
            "echo $(exec {}) suffix", "(true); exec {}",
        )
        for wrapper in wrappers:
            for command, fixed, expected in (
                ("gh pr view 1", 'gh pr view 1 --repo "$workflow_host/$workflow_repo"', "valued --repo"),
                ("python3 -u", '"${python_cmd[@]}" -u', "direct python3"),
            ):
                with self.subTest(wrapper=wrapper, command=command), tempfile.TemporaryDirectory(prefix="workflow-prefix-") as directory:
                    root = self.root(directory, references={"examples.md": "```bash\n" + wrapper.format(command) + "\n```\n"})
                    findings = check_repository(root)
                    self.assertTrue(any(item.line == 2 and expected in item.message for item in findings), [str(item) for item in findings])
                    (root / "skills/github-workflow/references/examples.md").write_text("```bash\n" + wrapper.format(fixed) + "\n```\n", encoding="utf-8")
                    self.assertEqual(check_repository(root), [])

    def test_execution_prefix_words_in_data_do_not_start_commands(self):
        examples = (
            "printf %s env gh pr view 1", "printf %s command gh pr view 1",
            "printf %s NAME=value gh pr view 1", "printf %s { gh pr view 1",
            "printf %s exec python3 -u", "echo $(true) gh pr view 1",
            "echo $(echo $(true)) python3 -u", "exec -a gh true",
            "exec -a python3 true", "env -u gh true", "env -C python3 true",
            "command -v gh", "command -pV python3", "command -pv gh",
            "args=(exec gh pr view 1)", "args+=(env python3 -u)",
            "printf %s NAME=$(true) gh pr view 1",
            "echo $(true) env gh pr view 1", "echo $(true) command python3 -u",
            "case x in (gh) true;; (python3) true;; esac",
            "case x in gh) true;; python3) true;; esac",
            "case x in (gh|python3) true;; esac",
            "echo $(case x in (x) true;; esac) gh pr view 1",
        )
        for command in examples:
            with self.subTest(command=command), tempfile.TemporaryDirectory(prefix="workflow-prefix-data-") as directory:
                root = self.root(directory, references={"examples.md": "```bash\n" + command + "\n```\n"})
                self.assertEqual(check_repository(root), [])

    def test_unknown_execution_prefix_options_are_not_silently_accepted(self):
        for wrapper in ("exec --future", "command -x", "env --future", "time -o log.txt", "env -S"):
            for command in ("gh pr view 1", "python3 -u"):
                with self.subTest(wrapper=wrapper, command=command), tempfile.TemporaryDirectory(prefix="workflow-prefix-unknown-") as directory:
                    root = self.root(directory, references={"examples.md": f"```bash\n{wrapper} {command}\n```\n"})
                    findings = check_repository(root)
                    self.assertTrue(any(item.line == 2 and "unsupported execution prefix" in item.message for item in findings), [str(item) for item in findings])

    def test_coprocess_execution_is_explicitly_outside_the_supported_grammar(self):
        for command in (
            "coproc gh pr view 1",
            "coproc worker { gh pr view 1; }",
            "if true; then coproc python3 check.py; fi",
        ):
            with self.subTest(command=command), tempfile.TemporaryDirectory(prefix="workflow-coproc-") as directory:
                root = self.root(directory, references={"boundary.md": f"```bash\n{command}\n```\n"})
                findings = check_repository(root)
                self.assertTrue(any(item.line == 2 and "unsupported coprocess" in item.message for item in findings), [str(item) for item in findings])
        with tempfile.TemporaryDirectory(prefix="workflow-coproc-data-") as directory:
            root = self.root(directory, references={"boundary.md": "Use the `coproc` keyword.\n```bash\nprintf '%s' coproc\nvalues=(coproc)\n```\n"})
            self.assertEqual(check_repository(root), [])

    def test_literal_here_document_bodies_are_data_and_following_commands_are_checked(self):
        headers = (
            ("cat <<'EOF'", "EOF"),
            ('cat <<"EOF"', "EOF"),
            ('cat <<-"EOF"', "\tEOF"),
            ('"${python_cmd[@]}" - <<\'EOF\'', "EOF"),
        )
        for header, delimiter in headers:
            with self.subTest(header=header), tempfile.TemporaryDirectory(prefix="workflow-here-body-") as directory:
                body = f"```bash\n{header}\ngh pr view 1\npython3 check.py\n{delimiter}\ngh issue view 2\n```\n"
                root = self.root(directory, references={"boundary.md": body})
                findings = check_repository(root)
                self.assertEqual([(item.line, item.message) for item in findings], [(6, "gh issue view needs a valued --repo")])
        with tempfile.TemporaryDirectory(prefix="workflow-here-quoted-data-") as directory:
            root = self.root(directory, references={"boundary.md": "```bash\nprintf '%s' \"<<'EOF'\"\nprintf '%s' prefix\"<<EOF\"\nprintf done;# <<'EOF'\n```\n"})
            self.assertEqual(check_repository(root), [])

    def test_here_document_header_comments_cannot_continue_past_the_body_boundary(self):
        for recipient in ("cat", '"${python_cmd[@]}" -'):
            with self.subTest(recipient=recipient), tempfile.TemporaryDirectory(prefix="workflow-here-comment-") as directory:
                body = f"```bash\n{recipient} <<'EOF' # comment " + "\\\nEOF\ngh pr view 1\ncat <<'EOF'\nEOF\n```\n"
                root = self.root(directory, references={"boundary.md": body})
                findings = check_repository(root)
                self.assertEqual([(item.line, item.message) for item in findings], [(4, "gh pr view needs a valued --repo")])

    def test_here_document_headers_preserve_real_continuations(self):
        for recipient in ("cat", '"${python_cmd[@]}" -'):
            with self.subTest(recipient=recipient), tempfile.TemporaryDirectory(prefix="workflow-here-continuation-") as directory:
                body = f"```bash\n{recipient} <<" + "\\\n'EOF'\ngh pr view 1\nEOF\ngh issue view 2\n```\n"
                root = self.root(directory, references={"boundary.md": body})
                findings = check_repository(root)
                self.assertEqual([(item.line, item.message) for item in findings], [(6, "gh issue view needs a valued --repo")])

    def test_ambiguous_here_document_consumers_and_delimiters_fail_explicitly(self):
        for header in (
            "bash <<'EOF'",
            "sh <<'EOF'",
            "cat <<'EOF' | bash",
            "cat <<'EOF'; bash",
            "cat <<EOF",
            'cat <<"$end"',
            "cat <<'FIRST' <<'SECOND'",
            "unknown_consumer <<'EOF'",
        ):
            with self.subTest(header=header), tempfile.TemporaryDirectory(prefix="workflow-here-unsupported-") as directory:
                root = self.root(directory, references={"boundary.md": f"```bash\n{header}\ntrue\nEOF\n```\n"})
                findings = check_repository(root)
                self.assertTrue(any(item.line == 2 and "unsupported here-document header" in item.message for item in findings), [str(item) for item in findings])

    def test_here_document_boundaries_cannot_hide_later_markdown_or_commands(self):
        for body in (
            "```bash\ncat <<'EOF'\ntext\n```\n",
            "```bash\ncat <<'EOF'\ntext\n",
            "```bash\ncat <<'EOF'\ntext\n EOF\n```\n",
            "```bash\ncat <<'EOF'\ntext\nEOF \n```\n",
        ):
            with self.subTest(body=body), tempfile.TemporaryDirectory(prefix="workflow-here-boundary-") as directory:
                root = self.root(directory, references={"boundary.md": body})
                findings = check_repository(root)
                self.assertTrue(any(item.line == 2 and "unterminated here-document" in item.message for item in findings), [str(item) for item in findings])
        with tempfile.TemporaryDirectory(prefix="workflow-here-reset-") as directory:
            root = self.root(directory, references={"boundary.md": "```bash\ncat <<'EOF'\ntext\n```\n\n```bash\ngh pr view 1\n```\n"})
            findings = check_repository(root)
            self.assertTrue(any(item.line == 7 and "valued --repo" in item.message for item in findings), [str(item) for item in findings])

    def test_only_active_guidance_is_scanned(self):
        with tempfile.TemporaryDirectory(prefix="workflow-scope-") as directory:
            root = self.root(directory)
            history = root / "skills/github-workflow/templates/history"
            history.mkdir(parents=True)
            (history / "AGENTS-v1.md").write_text("```bash\ngh issue view 1\n```\n", encoding="utf-8")
            (root / "skills/github-workflow/templates/AGENTS.md").write_text("```bash\npython3 - x.py\n```\n", encoding="utf-8")
            self.assertEqual(check_repository(root), [])

    def test_cli_returns_nonzero_with_file_and_line(self):
        with tempfile.TemporaryDirectory(prefix="workflow-cli-") as directory:
            root = self.root(directory, references={"examples.md": "```bash\ngh issue view 1\n```\n"})
            result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("skills/github-workflow/references/examples.md:2:", result.stderr)
            (root / "skills/github-workflow/references/examples.md").write_text("", encoding="utf-8")
            result = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
