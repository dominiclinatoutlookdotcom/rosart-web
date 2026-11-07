---
name: publish
description: Publish the Rosart website (rosart.com.au) from the current Shopify content. Use when a staff member asks to "publish the website", "update rosart.com.au", "push the Shopify changes live", "put my product changes on the website", says "the website is out of date" or "the site doesn't show my change", or asks how to publish. Runs the preflight, shows what will change, publishes after a yes, and commits the export locally.
---

# Publish the Rosart website

You are helping a Rosart staff member who is not a developer. Speak in plain words: no jargon,
no stack traces. Content is edited in **Shopify only**; this flow copies it to rosart.com.au.

Run every command from the repo root with the Bash tool. Steps 2 and 3 take minutes: give them
`timeout: 600000`; if a run is cut off by the timeout, run it again with `run_in_background`.

## Step 1: preflight

Run `make doctor`. It prints `PASS`, `WARN` or `FAIL` lines, each problem with a `fix:` line.

- All `FAIL` lines must be fixed before going on. Fix them **in order, one at a time**, using
  only the `fix:` line, then run `make doctor` again.
- Run a fix yourself when it is a plain command (`npm ci`, `git pull --ff-only`,
  `git switch main`, a `winget install`). Before `git restore` or `git clean`, tell the staff
  member what will be dropped and ask first.
- Stop and ask the staff member for anything only they can do:
  - **AWS key** (`aws configure --profile rosart-deploy`): they run it themselves in their own
    terminal and type the key the owner gave them. Never ask them to paste the key into chat.
  - **Shopify sign-in** (`shopify store auth ...` or `shopify auth login`): it opens a browser;
    they sign in and approve there. Run the exact command from the `fix:` line, unchanged.
  - Installing a program, or closing and reopening the terminal.
- If a `fix:` line says to contact the owner, stop: tell the staff member that, and why.
- `WARN` lines do not block. Mention them in one line (for example "2 earlier publishes are
  waiting for the owner to upload them to GitHub").

## Step 2: preview

Run `make publish-dryrun`. It downloads the current content from Shopify (read-only), builds the
site, runs every check, and shows what the upload would do. It never changes the live site.

If it fails, go to **When a check fails**. If it passes, run `make changes` and summarise for the
staff member in plain words:

- what changed in Shopify, by name: products, pages, collections, menus, theme editor areas
  (homepage, header, footer...), and image counts (from `make changes`);
- the upload plan from the last lines of the dry run: how many files would be uploaded and
  deleted, and the deleted pages by name. The upload count is normally close to every file,
  because every build is new; the content list above is what really changed. Deletions matter:
  name each deleted page.

If nothing changed in Shopify and nothing would be deleted, say there is nothing new to publish
and ask whether they still want to publish. Otherwise ask: "Publish these changes to
rosart.com.au now?" Wait for a clear yes.

## Step 3: publish

On a clear yes, run exactly:

```sh
make publish-only PUBLISH_FLAGS="--yes --wait"
```

This publishes the content downloaded in step 2 (it does not download again), runs every check
again first, uploads the site, and clears the web cache. Do not use `make publish`: it downloads
again and could publish changes the staff member has not seen.

Report in plain words: done or not, the CloudFront invalidation ID from the line
`CloudFront invalidation <ID> ...`, and the pages to spot-check: `https://rosart.com.au/` plus the
URLs listed by `make changes`. Changes can take a few minutes to show; a hard refresh helps.

If the upload fails partway (`sync N/6 failed`), stop and tell the staff member to contact the
owner now: the live site may be partly updated. Do not retry on your own.

## Step 4: record it

After a successful publish, run `make publish-commit`. It saves the downloaded content in git on
this PC as `Publish <date> by <name>`, with what changed listed. Tell the staff member it is saved
locally and **the owner will upload it to GitHub**. Never push.

## When a check fails

Explain the failure in plain words, say what to do in Shopify, then **stop**. After they fix it
in Shopify, start again from step 1.

| The output says | Tell the staff member |
| --- | --- |
| `email address <x> is not the site email info@rosart.com.au` (with a page) | That page (or product) in Shopify shows another email address. Edit it in the Shopify admin to use info@rosart.com.au. |
| `Collections "<a>" and "<b>" both map to /collections/...` | Two collections have titles that make the same web address. Rename one in Shopify. |
| `broken reference <x>` or `missing page` | Name the page and the link or image. If something was deleted or renamed in Shopify, restore or fix it there; otherwise contact the owner. |
| `theme ...`, `site renders ...`, `is missing on the site`, `no fidelity rule`, `ALLOWED_DIFFERENCES`, or a failing `fidelity` test | The theme was changed in the Shopify theme editor in a way the website does not support yet. Stop and contact the owner (send them the failing lines). |
| `image is ... KB, over the 600 KB limit` | Name the image; contact the owner. |
| `shopify theme pull exited` or a Shopify sign-in error | Run `make doctor` and follow its Shopify fix. |
| `aws ... failed` before any upload | Run `make doctor` and follow its AWS fix. |
| Anything else | Quote the key line, say you cannot fix it, and ask them to contact the owner. |

## Never

- Never edit code, tests, `src/lib/allowed-differences.ts` (the theme allowlist),
  `deploy.config.json`, the Makefile or anything in `export/` to make a check pass. The fix is
  always in Shopify, or it is the owner's.
- Never pass flags or set variables that skip checks; run only the commands above.
- Never sign in to Shopify with write scopes, run `shopify store execute` with
  `--allow-mutations`, or run `make seo-apply` or `tools/seo_apply.py`.
- Never `git push`, `git commit` anything but `make publish-commit`, or discard changes outside
  `export/`.
- Never ask for, paste, print or read keys: not in chat, not `~/.aws/credentials`, not
  `aws configure list` output.
