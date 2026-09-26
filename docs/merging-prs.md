# Merging pull requests

A checklist for merging a collaborator's PR into `main`. Each step names the
failure it prevents; the examples are from PRs #9 and #10 (the movie companion).

## Before merging

1. **Read the file list, not just the title.** Look for the three kinds of file
   that need extra care:
   - a **migration** (`django_app/*/migrations/`): it changes a real database;
   - a **shared template or stylesheet** (`base.html`, `theoria.css`): it affects
     every page;
   - **`requirements.txt`**: it is the web runtime file, and Vercel installs it
     into a bundle capped at 500 MB.

2. **Check the base branch.** `gh pr view N --json baseRefName`. A stacked PR
   (#10 was based on #9's branch, not `main`) would merge into the wrong place.

3. **Run the full test suite on the branch**, not only the tests the author
   ran: `gh pr checkout N && pytest`. #9 passed its own 32 tests but broke two
   existing ones, because its new CSRF token changed a per-page token count.

4. **Read any failing check's logs before deciding what it means.** Both PRs
   showed a red Vercel check. The log
   (`npx vercel inspect <deployment-id> --logs`) showed preview builds lack
   `AUTH_DATABASE_URL`, so it said nothing about the code. Never merge past a
   red check unread, and never block on one unread.

5. **If the PR adds a migration, apply it to Neon *before* merging.**
   Merging to `main` triggers a Vercel deploy, and Django never migrates the
   warehouse or the auth database on deploy. See `CLAUDE.md` (Hosting). The
   auth database is Neon `theoria_auth`; local dev uses SQLite, so `migrate`
   locally does not touch it.

6. **If the PR needs a new environment variable, set it in Vercel first**
   (e.g. `GEMINI_API_KEY`). Otherwise the feature silently falls back or fails.

7. **Skim for what the project rules forbid.** Hard-coded English in
   JavaScript or templates (the site ships en/ru/uz), UI copy that says
   "Films" or "titles" instead of "Movies", and internal table or file names
   in user-facing text. Flag these; don't block on them unless they break
   something.

## Merging

8. **Pick the merge type on purpose.**

   | Type | Result | Use when |
   |---|---|---|
   | Merge commit (`--merge`) | Keeps every commit, adds a merge commit | PRs are stacked |
   | Squash (`--squash`) | One commit on `main` | A single PR with noisy commits |
   | Rebase (`--rebase`) | Linear history | You want no merge commits |

   Squashing the bottom PR of a stack rewrites its commit, so the PR above it
   then shows conflicts. Use a merge commit for stacks.

9. **Merge stacked PRs bottom-up.** After the first merges, retarget the next:
   `gh pr edit N --base main`. Don't delete the merged branch until the PR above
   it is retargeted.

10. **If you must fix the PR yourself,** prefer asking the author. A small
    mechanical fix (a test count) can go on their branch as a separate commit,
    so their work stays visible.

## After merging

11. **Pull `main` and run the full suite again.** The merged result is a state
    nobody has tested yet: `git pull --ff-only && pytest`.

12. **Watch the production deploy**, and confirm the feature works if it
    touches a database or an external API.

13. **Note follow-ups you chose not to block on** (translations, copy) so they
    don't get lost.

## Commands

```bash
gh pr list --state open
gh pr view N --json baseRefName,files,statusCheckRollup
gh pr diff N
gh pr checkout N && pytest
gh pr merge N --merge
gh pr edit N --base main          # retarget a stacked PR
git checkout main && git pull --ff-only && pytest
```
