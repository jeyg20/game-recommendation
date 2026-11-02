#!/usr/bin/env bash
# Create an isolated checkout for one task agent: a worktree on feat/<id>-<slug> off ai-created, with its own
# copy of .env and data/ (neither is in git, and config.ROOT points at the checkout), and a synced .venv.
#
#   scripts/agent_worktree.sh f01 ratings      -> ../gamer_guide-f01 on feat/f01-ratings
#   scripts/agent_worktree.sh --remove f01     -> removes that worktree (the branch is kept)
set -euo pipefail

repo=$(git rev-parse --show-toplevel)
if [[ ${1:-} == --remove ]]; then
    git -C "$repo" worktree remove --force "$repo/../gamer_guide-$2"
    exit
fi
id=${1:?task id, e.g. f01} slug=${2:?slug, e.g. ratings}
dir="$repo/../gamer_guide-$id"
branch="feat/$id-$slug"

git -C "$repo" worktree add "$dir" -b "$branch" ai-created
(cd "$dir" && "$repo/scripts/worktree_setup.sh")
echo "on $branch"
