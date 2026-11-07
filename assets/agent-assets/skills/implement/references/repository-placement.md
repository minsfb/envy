# Repository and workspace placement

First identify the affected repository and native VCS. A plain organizational
directory may hold several repositories: inspect the relevant children instead
of initializing or branching the workspace itself. Preserve an existing layout.
For new multi-repository projects, prefer a project workspace with sibling
checkouts. Concrete development storage roots belong in environment bundles.

## Git

Use the shared helper, resolving its path against this skill directory:

```sh
node /resolved/implement/scripts/git-worktree.mjs plan --cwd /path/to/checkout --branch feature/task
node /resolved/implement/scripts/git-worktree.mjs prepare --cwd /path/to/checkout --branch feature/task
```

`plan` is read-only. `prepare` creates only the owning primary checkout's
`.worktrees/` parent and appends `/.worktrees/` to its `.gitignore` when needed.
It preserves existing comments, ignore rules, worktrees and branches; it never
stages or commits. Include an intended ignore change in the ordinary source
commit, following the repository's instructions. Then use native `git worktree
add -b BRANCH WORKTREE BASE` with the returned absolute `worktree` path and a
fresh branch/destination. Branch slashes become hyphens in the directory name;
if that collides with retained work, choose another fresh name or explicit path.

The helper resolves Git's primary checkout using checkout/common-directory
identity, the worktree registry and shared `core.worktree` configuration. It
verifies the shared Git metadata and handles linked worktrees and ordinary submodules without
assuming `.git` is a directory. A call from `repo/.worktrees/task-a` places the
next task at `repo/.worktrees/task-b`, never under task-a. Do not create task
working files under `.git`, which Git owns as administrative storage.

Bare repositories, missing primary checkouts and ambiguous layouts require an
explicit placement choice. Existing explicit destinations remain supported;
do not silently relocate one after a permission failure. The owning checkout
must be writable by the active harness; use its normal startup configuration to
grant that scope when needed. Do not bypass a sandbox or change ownership.

Independent consumers may distribute byte-identical copies of this helper,
binding their copy to the canonical source instead of maintaining another root
resolver. The helper requires Node 18+. If Node is unavailable, use native
Git to inspect `git worktree list --porcelain`, verify the primary checkout and
common Git directory, and prepare the same root-scoped ignore/directory rule.
Never substitute the current linked worktree or infer the owner from the parent
of a Git metadata directory.

## Other VCSs

Use the native isolation procedure supplied by repository instructions or an
environment bundle. Git worktree commands and metadata conventions do not apply
to other VCSs. Resolve the current checkout's actual root before placing a sibling
native clone; avoid naming assumptions based on a high-level workspace directory.
