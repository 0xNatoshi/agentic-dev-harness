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
