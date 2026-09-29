#!/usr/bin/env bash
# Read-only checks. Exit 0: clear; 1: blocker; 2: unreadable or invalid evidence.
# Modes: origin, suspension, reviews, pages, identity (predicted server-generated
# merge author before merge; exit 0 prints only the noreply) and published (the
# merged PR's commit author and committer, booleans only).
# Requires Bash, awk, Git, gh and Python 3.8+; no external jq.
set -u
fail() { printf '%s\n' "$*" >&2; exit 2; }
mode=${1:-}; shift || fail 'Expected origin, suspension, reviews, pages, identity or published.'

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || fail 'Cannot locate skill scripts.'
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; assert sys.version_info >= (3,8)' 2>/dev/null; then
  python_cmd=(python3)
elif command -v py >/dev/null 2>&1 && py -3 -c 'import sys; assert sys.version_info >= (3,8)' 2>/dev/null; then
  python_cmd=(py -3)
elif command -v python >/dev/null 2>&1 && python -c 'import sys; assert sys.version_info >= (3,8)' 2>/dev/null; then
  python_cmd=(python)
else
  fail 'Python 3.8+ is required for Unicode/origin checks; install Python 3 on Windows and reopen Git Bash.'
fi
case "$mode" in
  origin|suspension|identity|published)
    case "$mode" in identity|published) command -v gh >/dev/null 2>&1 || fail 'GitHub CLI (gh) is required.' ;; esac
    "${python_cmd[@]}" "$script_dir/workflow-context.py" "$mode" "$@"
    exit $?
    ;;
  reviews|pages)
    command -v gh >/dev/null 2>&1 || fail 'GitHub CLI (gh) is required.'
    [ "$#" -ge 2 ] || fail 'Expected owner and repository.'
    owner=$1; repo=$2
    origin_info=$("${python_cmd[@]}" "$script_dir/workflow-context.py" origin "$owner/$repo") || exit 2
    IFS=$'\t' read -r workflow_host workflow_repo workflow_default <<< "$origin_info"
    response=$(mktemp) || fail 'Cannot allocate response file.'
    trap 'rm -f "$response"' EXIT
    ;;
  *) fail 'Expected origin, suspension, reviews, pages, identity or published.' ;;
esac

if [ "$mode" = reviews ]; then
  [ "$#" -eq 3 ] || fail 'Expected owner, repository and PR number.'
  case "$3" in ''|*[!0-9]*) fail 'Invalid PR number.' ;; esac
  gh api --hostname "$workflow_host" graphql --paginate -F owner="$owner" -F name="$repo" -F n="$3" -f query='query($owner:String!,$name:String!,$n:Int!,$endCursor:String){repository(owner:$owner,name:$name){pullRequest(number:$n){reviewThreads(first:100,after:$endCursor){nodes{isResolved}pageInfo{hasNextPage,endCursor}}}}}' --jq '
    if ((.errors // []) | length) == 0
      and (.data.repository.pullRequest.reviewThreads.nodes | type) == "array"
      and (.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage | type) == "boolean"
    then .data.repository.pullRequest.reviewThreads |
      if .pageInfo.hasNextPage and ((.pageInfo.endCursor | type) != "string" or .pageInfo.endCursor == "")
      then error("Missing next cursor")
      else [.pageInfo.hasNextPage, ([.nodes[] | select(.isResolved != true)] | length)] | @tsv end
    else error("Invalid review-thread response") end' > "$response" || fail 'Review API read or JSON validation failed.'
  awk '
    $0 !~ /^(true|false)\t[0-9]+$/ { invalid=1 }
    NR > 1 && previous != "true" { invalid=1 }
    { previous=$1; pending+=$2 }
    END {
      if (!NR || invalid || previous != "false") exit 2
      print "Unresolved review threads: " pending
      if (pending != 0) exit 1
    }
  ' "$response"
  exit $?
fi

[ "$#" -eq 2 ] || fail 'Expected owner and repository.'
# --include exposes the actual HTTP status; never classify stderr text as a 404.
gh api --hostname "$workflow_host" "repos/$workflow_repo/pages" --include > "$response"
status=$?
http=$(awk 'NR == 1 && $1 ~ /^HTTP\// && $2 ~ /^[0-9][0-9][0-9]$/ { print $2 }' "$response")
if [ "$status" -eq 0 ] && [ "$http" = 200 ]; then
  printf '%s\n' 'Pages is configured: establish deployment impact before merge.'
  exit 1
elif [ "$status" -ne 0 ] && [ "$http" = 404 ]; then
  printf '%s\n' 'Pages endpoint returned HTTP 404 (no Pages configuration reported).'
  exit 0
fi
fail "Pages check failed or returned an unexpected status (gh=$status, HTTP=${http:-unknown})."
