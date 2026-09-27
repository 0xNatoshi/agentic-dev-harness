#!/usr/bin/env bash
# Read-only final worktree guard before deleting a verified local branch ref.
# Exit 0: unused; 1: in use; 2: inventory or operation state cannot be trusted.

branch=${1-}
if [ -z "$branch" ] || [ "$#" -ne 1 ] || [[ "$branch" == refs/heads/* ]] || ! git check-ref-format "refs/heads/$branch" >/dev/null 2>&1; then
  printf '%s\n' 'Expected one valid, short branch name.' >&2
  exit 2
fi
ref="refs/heads/$branch"
common=$(git rev-parse --path-format=absolute --git-common-dir) || exit 2
[ -n "$common" ] || exit 2

inventory=$(mktemp) || exit 2
inventory_z=$(mktemp) || { rm -f "$inventory"; exit 2; }
trap 'rm -f "$inventory" "$inventory_z"' EXIT

# Retain the exact attached-branch check used by the previous guarded command.
git worktree list --porcelain > "$inventory" || exit 2
if grep -Fx "branch $ref" "$inventory"; then
  printf '%s\n' 'Branch is still checked out by a worktree; retain it.' >&2
  exit 1
else
  status=$?
  [ "$status" -eq 1 ] || exit 2
fi

# -z keeps worktree paths unambiguous, including whitespace and newlines.
git worktree list --porcelain -z > "$inventory_z" || exit 2

check_operation_file() {
  operation=$1
  file=$2
  [ -f "$file" ] && [ -r "$file" ] || {
    printf 'Cannot read %s state: %s\n' "$operation" "$file" >&2
    exit 2
  }
  state=$(cat "$file") || exit 2
  [ -n "$state" ] || {
    printf 'Empty %s state: %s\n' "$operation" "$file" >&2
    exit 2
  }
  if [ "$state" = "$ref" ] || [ "$state" = "$branch" ]; then
    printf 'Branch is in use by %s in %s; retain it.\n' "$operation" "$worktree" >&2
    exit 1
  fi
  # Git writes this exact literal as head-name when rebasing a detached HEAD.
  [ "$operation" != bisect ] && [ "$state" = "detached HEAD" ] && return 0
  case "$state" in
    refs/heads/*) state_ref=$state ;;
    *) state_ref="refs/heads/$state" ;;
  esac
  git check-ref-format "$state_ref" >/dev/null 2>&1 || {
    printf 'Unknown %s state: %s\n' "$operation" "$file" >&2
    exit 2
  }
}

check_am_head() {
  if am_head=$(git -C "$worktree" symbolic-ref -q HEAD); then
    [ "$am_head" != "$ref" ] || {
      printf 'Branch is in use by git am in %s; retain it.\n' "$worktree" >&2
      exit 1
    }
  else
    # Status 1 is a detached HEAD; anything else cannot be trusted.
    [ "$?" -eq 1 ] || exit 2
  fi
}

check_operation_dir() {
  operation=$1
  directory="$gitdir/$operation"
  if [ -e "$directory" ] || [ -L "$directory" ]; then
    [ -d "$directory" ] && [ -r "$directory" ] && [ -x "$directory" ] || {
      printf 'Cannot read %s state in %s\n' "$operation" "$gitdir" >&2
      exit 2
    }
    # rebase --update-refs lists branches Git rewrites when the rebase ends.
    updates="$directory/update-refs"
    if [ -e "$updates" ] || [ -L "$updates" ]; then
      [ -f "$updates" ] && [ ! -L "$updates" ] && [ -r "$updates" ] || {
        printf 'Cannot read %s update-refs in %s\n' "$operation" "$gitdir" >&2
        exit 2
      }
      # Git writes one ref, old OID, new OID triplet per branch. A record that
      # is empty, truncated or malformed cannot prove the branch is absent.
      awk -v ref="$ref" '
        NR % 3 == 1 { if ($0 == ref) found = 1; if ($0 !~ /^refs\//) bad = 1; next }
        (length($0) != 40 && length($0) != 64) || /[^0-9a-f]/ { bad = 1 }
        END { if (bad || NR == 0 || NR % 3 != 0) exit 2; exit found ? 1 : 0 }
      ' "$updates"
      case $? in
        0) ;;
        1)
          printf 'Branch is in use by %s --update-refs in %s; retain it.\n' "$operation" "$worktree" >&2
          exit 1
          ;;
        *)
          printf 'Malformed %s update-refs in %s\n' "$operation" "$gitdir" >&2
          exit 2
          ;;
      esac
    fi
    # git am uses rebase-apply with an applying marker and no head-name; it
    # applies onto the worktree's own HEAD, which the attached check covers.
    if [ "$operation" = rebase-apply ] && [ -f "$directory/applying" ] && [ ! -L "$directory/applying" ] &&
      [ ! -e "$directory/rebasing" ] && [ ! -L "$directory/rebasing" ] &&
      [ ! -e "$directory/head-name" ] && [ ! -L "$directory/head-name" ]; then
      check_am_head
      return 0
    fi
    check_operation_file "$operation" "$directory/head-name"
  fi
}

seen=0
while IFS= read -r -d '' entry; do
  case "$entry" in
    worktree\ *)
      worktree=${entry#worktree }
      [ -n "$worktree" ] || exit 2
      seen=$((seen + 1))
      gitdir=$(git -C "$worktree" rev-parse --absolute-git-dir) || exit 2
      worktree_common=$(git -C "$worktree" rev-parse --path-format=absolute --git-common-dir) || exit 2
      [ "$worktree_common" = "$common" ] || {
        printf 'Worktree no longer belongs to this repository: %s\n' "$worktree" >&2
        exit 2
      }
      [ -d "$gitdir" ] && [ -r "$gitdir" ] && [ -x "$gitdir" ] || exit 2
      ls -A "$gitdir" >/dev/null || exit 2
      check_operation_dir rebase-merge
      check_operation_dir rebase-apply
      bisect="$gitdir/BISECT_START"
      if [ -e "$bisect" ] || [ -L "$bisect" ]; then
        check_operation_file bisect "$bisect"
      fi
      ;;
    "branch $ref")
      printf '%s\n' 'Branch became checked out by a worktree; retain it.' >&2
      exit 1
      ;;
  esac
done < "$inventory_z"
[ "$seen" -gt 0 ] || {
  printf '%s\n' 'Empty worktree inventory; retain the branch.' >&2
  exit 2
}
