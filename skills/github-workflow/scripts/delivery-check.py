#!/usr/bin/env python3
"""Report mechanical readiness of one pull request without merging it."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
PROJECT = Path.cwd()
REVIEW = re.compile(
    r"^\s*-\s*Independent review:\s*(?:not required\s*\([^)]*\)|"
    r"done on\s+[0-9a-f]{7,40};\s*findings:\s*(?:none|fixed|justified)(?:\s+.*)?)\s*$",
    re.M,
)
THREAD_QUERY = """query($owner:String!,$repo:String!,$number:Int!,$endCursor:String){
  repository(owner:$owner,name:$repo){
    pullRequest(number:$number){
      reviewThreads(first:100,after:$endCursor){
        pageInfo{hasNextPage endCursor}
        nodes{isResolved}
      }
    }
  }
}"""


def run(args):
    return subprocess.run(args, cwd=PROJECT, capture_output=True, text=True)


def output(message):
    print(json.dumps(message, ensure_ascii=False, sort_keys=True))


def fail(message):
    output({"error": message})
    raise SystemExit(2)


def origin():
    result = run(["bash", str(SKILL_DIR / "merge-preflight.sh"), "origin"])
    if result.returncode != 0:
        fail("origin preflight failed")
    fields = result.stdout.rstrip("\n").split("\t")
    if len(fields) != 3 or any(not field for field in fields):
        fail("origin preflight returned malformed data")
    return fields


def json_pages(text):
    decoder = json.JSONDecoder()
    items = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index == len(text):
            break
        item, index = decoder.raw_decode(text, index)
        items.append(item)
    return items


def gh_json(args, runner):
    result = runner(["gh"] + args)
    if result.returncode != 0:
        fail("gh command failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        fail("gh returned invalid JSON")


def pr_number(pr):
    value = str(pr).rstrip("/")
    if value.isdigit():
        return value
    value = value.rsplit("/", 1)[-1]
    if not value.isdigit():
        fail("PR selector must be a number or a pull-request URL")
    return value


def check_pr(pr, repo, default_branch, runner=run):
    number = pr_number(pr)
    view = gh_json(
        [
            "pr",
            "view",
            str(pr),
            "--repo",
            repo,
            "--json",
            "state,isDraft,mergeable,mergeStateStatus,reviewDecision,headRefOid,baseRefName,body,url",
        ],
        runner,
    )
    blockers = []
    warnings = []

    if view.get("state") != "OPEN":
        blockers.append("PR is not open")
    if view.get("isDraft"):
        blockers.append("PR is still a draft")
    if view.get("mergeable") != "MERGEABLE":
        blockers.append("PR is not mergeable")
    merge_state = view.get("mergeStateStatus", "")
    if merge_state not in {"CLEAN", "HAS_HOOKS", "UNSTABLE"}:
        blockers.append("merge state is not ready: " + str(merge_state or "missing"))
    elif merge_state == "UNSTABLE":
        warnings.append("merge state is UNSTABLE; non-required checks are not green")
    review = view.get("reviewDecision", "")
    if review in {"REVIEW_REQUIRED", "CHANGES_REQUESTED"}:
        blockers.append("review decision is " + review)
    elif review not in {"APPROVED", ""}:
        blockers.append("review decision is not recognized: " + str(review))
    if view.get("baseRefName") != default_branch:
        blockers.append("PR base is not the verified default branch")
    if not VIEW_REVIEW_OK(view.get("body", "")):
        blockers.append("PR body has no valid Independent review line")
    if "TO FILL" in view.get("body", ""):
        blockers.append("PR body still contains TO FILL")

    head = view.get("headRefOid", "")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        blockers.append("PR head is not a full commit SHA")
    else:
        exists = runner(["git", "cat-file", "-e", head + "^{commit}"])
        if exists.returncode != 0:
            blockers.append("PR head is not available locally after fetch")
        else:
            ancestor = runner(["git", "merge-base", "--is-ancestor", "origin/" + default_branch, head])
            if ancestor.returncode != 0:
                blockers.append("PR head is not current with the verified default branch")

    checks_result = runner(
        ["gh", "pr", "checks", str(pr), "--repo", repo, "--required", "--json", "name,state,bucket"]
    )
    if checks_result.returncode != 0:
        if checks_result.returncode == 1 and "no required checks reported" in (
            checks_result.stdout + checks_result.stderr
        ):
            all_result = runner(
                ["gh", "pr", "checks", str(pr), "--repo", repo, "--json", "name,state,bucket"]
            )
            if all_result.returncode != 0:
                fail("gh command failed")
            try:
                checks = json.loads(all_result.stdout)
            except json.JSONDecodeError:
                fail("gh returned invalid JSON")
            warnings.append("no required checks reported; evaluating all reported checks")
        else:
            fail("gh command failed")
    else:
        try:
            checks = json.loads(checks_result.stdout)
        except json.JSONDecodeError:
            fail("gh returned invalid JSON")
    if not checks:
        warnings.append("no required checks reported")
    else:
        for check in checks:
            bucket = check.get("bucket", "")
            if bucket in {"pass", "skipping", "neutral"}:
                if bucket != "pass":
                    warnings.append("required check is " + str(check.get("name", "?")) + ": " + bucket)
                continue
            blockers.append("required check is not green: " + str(check.get("name", "?")) + " (" + str(bucket) + ")")

    owner, _, name = repo.partition("/")
    pages = runner(
        [
            "gh",
            "api",
            "graphql",
            "--paginate",
            "-f",
            "query=" + THREAD_QUERY,
            "-F",
            "owner=" + owner,
            "-F",
            "repo=" + name,
            "-F",
            "number=" + number,
        ]
    )
    if pages.returncode != 0:
        fail("review-thread query failed")
    try:
        unresolved = 0
        for page in json_pages(pages.stdout):
            threads = page["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
            unresolved += sum(1 for thread in threads if not thread.get("isResolved"))
    except (KeyError, TypeError, json.JSONDecodeError):
        fail("review-thread query returned invalid data")
    if unresolved:
        blockers.append(str(unresolved) + " unresolved review thread(s)")

    return {
        "ready": not blockers,
        "pr": view.get("url", str(pr)),
        "head": head,
        "base": view.get("baseRefName"),
        "blockers": blockers,
        "warnings": warnings,
        "merge_command": "gh pr merge " + str(pr) + " --repo " + repo + " --<method> --match-head-commit " + head,
    }


def VIEW_REVIEW_OK(body):
    return bool(REVIEW.search(body or ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", required=True, help="Pull request number or URL")
    args = parser.parse_args()
    host, repo, default_branch = origin()
    if host != "github.com":
        fail("only github.com delivery checks are supported")
    fetch = run(["git", "fetch", "origin"])
    if fetch.returncode != 0:
        fail("git fetch origin failed")
    result = check_pr(args.pr, repo, default_branch)
    output(result)
    raise SystemExit(0 if result["ready"] else 1)


if __name__ == "__main__":
    main()
