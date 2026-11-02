#!/usr/bin/env bash
# Prepare a fresh worktree for an agent: copy .env and data/ from the main checkout (neither is in git, and
# config.ROOT points at the checkout) and sync .venv. Safe to rerun: an existing .env or data/ is left alone, so
# it never overwrites the agent's own copy. Run it from inside the worktree (T3 Code runs it on worktree create).
set -euo pipefail

here=$(git rev-parse --show-toplevel)
main=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")
if [[ $here == "$main" ]]; then
    echo "this is the main checkout, nothing to do" >&2
    exit
fi
[[ -e $here/.env ]] || cp "$main/.env" "$here/.env"
[[ -e $here/data ]] || cp -r "$main/data" "$here/data"  # a private copy: parallel agents never share a cache file
(cd "$here" && uv sync -q)
echo "ready: $here"
