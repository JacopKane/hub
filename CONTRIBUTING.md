# Contributing to hub

Every change reaches `main` through a pull request. `main` takes squash merges only. A PR needs no approval, but three checks must pass against the latest `main`: `linux`, `macos` and `privacy`.

## Before you open a PR

Branch from the latest `main`, and name the branch `feat/<slug>`, `fix/<slug>` or `docs/<slug>`.

Run the scenarios. You need Python 3.13, pytest 9.2.0 and tmux.

```sh
python3 -m pytest -q -p no:cacheprovider
```

Then run the two checks the `privacy` job runs:

```sh
git diff --name-only -z --diff-filter=ACMR origin/main...HEAD | xargs -0 -r python3 -I tests/scrub_scan.py
python3 -I tests/commit_check.py --squash origin/main..HEAD
```

Both print the file or commit, the line and the kind of each hit, never the value itself.

The `privacy` job takes both check scripts from `main`, not from your branch, and never runs your PR's copy. If your PR adds a check that `main` does not have yet, the job skips that check with a notice on every run of the PR and on the push that merges it. If neither `main` nor your PR has the check, the job fails.

## What the privacy job refuses

The shape scan looks at every file the PR adds or changes. It refuses home and temp paths, email addresses, UUIDs, local host names and private IP addresses, token shapes, the em dash, and links into chat, note and document pages. Tests and docs may use made-up values instead:

- the names `sam`, `alex` and `acme` in a home path
- an address at `example.com`, `example.org` or `example.net`
- a UUID whose first group is eight zeros
- a page link whose only id is eight or more zeros, such as `https://claude.ai/artifact/00000000`. It has no query or fragment, and no subdomain other than `www` or one of the names above.

The commit check looks at each commit on its own:

- If you commit with a GitHub noreply address, the author and committer dates must use the time zone `+0000`, so the commit does not show where you are. Commit with `TZ=UTC`.
- The message, trailers included, passes the same shape scan. GitHub noreply addresses are allowed there.
- A commit that changes `.hub/maintainers` or `.hub/revoked` changes nothing else.
- A commit that changes a file named in `.hub/checker-files` changes only those files, that list and the version in `plugins/hub/.claude-plugin/plugin.json`.
- No commit may add a symlink.
- A merge commit is checked only on the changes it makes itself, not on what it brings in from its parents.
- `main` takes squash merges, so the whole PR lands as one commit. The two rules about `.hub/` files therefore also hold for the PR as a whole. Put a change to `.hub/maintainers`, `.hub/revoked` or the checker's files in a PR of its own. `--squash` runs this part of the check.
