#!/usr/bin/env bash
# Save what a run left in state/ - the encrypted Google token, the note of an
# open gap - in the buyer's own repository. In the cloud that is the only place
# that outlives the runner: the next run starts from an empty machine and reads
# state/ to know what the last one found.
#
# Measured on 6 November 2026, against the step this script replaces
# (`git add -A state` ... `git pull --rebase --quiet` ... `git push`), in a
# sandbox repository with a bare remote:
#
# | what happened to the push | the step | the note of the gap |
# |---|---|---|
# | a commit landed on the branch in between | exit 1, rebase stopped in CONFLICT | never reached the remote |
# | the branch is protected ("Changes must be made through a pull request") | exit 1, `[remote rejected]` | never reached the remote |
#
# What that costs is the bug of 6 November all over again: with no note in
# state/, `ghti status` in the cloud exits 0 over missing months and writes
# "## Google authorization OK" on the run page, and the daily Reminder run goes
# green. Nothing rediscovers the gap on its own - `ghti gaps` asks intervals.icu,
# but no workflow is allowed to run it on a schedule, because a day nobody wore
# the watch looks exactly like a day that was lost. So the note is the memory,
# and losing it makes the silence final.
#
# Two changes: the runner's state/ is replayed on top of the branch instead of
# rebased onto it (no conflict to resolve with nobody there, and a push that
# simply lost a race is retried), and when it still cannot be saved the run page
# says which days are missing and how to close them, instead of a git error.
#
# Replaying means `git reset --hard origin/<branch>`, which moves the whole
# working tree, not only state/. Measured the same day, in the same sandbox,
# with a commit waiting on the branch: after the retry the runner holds the
# BRANCH's source files, while `GITHUB_SHA` on the run page still names the
# commit the job checked out. Three of the four workflows save state/ as their
# last step, so nothing of theirs runs on the swapped tree; `reauth.yml` had a
# step after it (`ghti sync`, the catch-up of the missed days), which would have
# run code the run page does not name. So the runner is put back on the commit
# it checked out as soon as the push is through, with the fresh state/ on top:
# the remote keeps the new commit, the job keeps its own code.
set -uo pipefail

branch="${GITHUB_REF_NAME:-$(git rev-parse --abbrev-ref HEAD)}"
attempts="${GHTI_SAVE_ATTEMPTS:-3}"
message="${1:-Update sync state (token, open gap)}"
#: The commit this job checked out. Later steps must keep running its code.
checked_out="$(git rev-parse HEAD)"

git add -A state
if git diff --cached --quiet; then
  echo "Nothing new in state/."
  exit 0
fi

# The runner's state/ is the freshest there is: the token it just refreshed, the
# gap it just measured. Kept aside so it wins over whatever is on the branch.
keep="$(mktemp -d)"
cp -R state/. "$keep"/

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git commit --quiet -m "$message"

for attempt in $(seq 1 "$attempts"); do
  if [ "$attempt" -gt 1 ]; then
    # The push lost: start again from the branch as it is now and put our
    # state/ back on top of it.
    git fetch --quiet origin "$branch" || true
    git reset --quiet --hard "origin/$branch"
    rm -rf state
    mkdir -p state
    cp -R "$keep"/. state/
    git add -A state
    if git diff --cached --quiet; then
      echo "state/ on $branch already says this."
      exit 0
    fi
    git commit --quiet -m "$message"
  fi
  if git push --quiet origin "HEAD:$branch"; then
    echo "Saved in state/ on $branch (push $attempt of $attempts)."
    if [ "$(git rev-parse HEAD^)" != "$checked_out" ]; then
      # The replay moved the tree onto the branch. The remote has the commit now;
      # give this job back the code it checked out, with the fresh state/ on top.
      git reset --quiet --hard "$checked_out"
      rm -rf state
      mkdir -p state
      cp -R "$keep"/. state/
      echo "Runner put back on the commit it checked out ($(git rev-parse --short HEAD))."
    fi
    exit 0
  fi
  echo "Push $attempt of $attempts did not go through."
done

# Out of attempts. Whoever gets the email for this red run must be able to see
# what was lost without reading git output.
page="${GITHUB_STEP_SUMMARY:-/dev/stdout}"
{
  echo "## What this run found could not be saved"
  echo
  echo "\`state/\` on \`$branch\` is where this bridge remembers things between runs,"
  echo "and $attempts pushes to it were refused. The next run starts from an empty"
  echo "machine, so what is below is lost unless you act on it."
  echo
  if [ -f "$keep/gap.json" ]; then
    resume=$(sed -n 's/.*"resume_from": *"\([0-9-]*\)".*/\1/p' "$keep/gap.json")
    through=$(sed -n 's/.*"through": *"\([0-9-]*\)".*/\1/p' "$keep/gap.json")
    if [ -n "$resume" ] && [ -n "$through" ]; then
      echo "### Days still missing on intervals.icu: **$resume to $through**"
      echo
      echo "A run stopped part-way and nothing has filled those days since. Copy the two"
      echo "dates now: no later run will name them again."
      echo
      echo "- **Actions > Sync > Run workflow**, with **from** = \`$resume\` and **to** = \`$through\`"
      echo "- On a computer: \`ghti backfill --from $resume --to $through\`"
      echo
    fi
  fi
  if [ -f "$keep/token.enc" ]; then
    echo "### The Google authorization was not saved either"
    echo
    echo "If Google handed this run a new refresh token, the one in \`state/\` no longer"
    echo "works and the next sync will stop on the authorization. **Actions > Re-authorize**"
    echo "fixes it."
    echo
  fi
  echo "### Why a push gets refused"
  echo
  echo "Usually the branch asks for a pull request, or this repository's Actions have"
  echo "read-only permissions (Settings > Actions > General > Workflow permissions)."
  echo "Both are settings of your own repository; nothing here can work around them."
} >> "$page"

exit 1
