# rosart-web

The public Rosart website (rosart.com.au): a static Astro site on S3 behind CloudFront, built
from a read-only export of the Rosart Shopify store (`rosart-mhfmyuxq.myshopify.com`). The
README is the full reference: build, checks, theme fidelity, publish, export, SEO plan.

## Content is edited in Shopify

Products, pages, collections and menus are edited in the Shopify admin; the homepage, Our Story,
header and footer text in the Shopify theme editor. The site is rebuilt from that content on
every publish. `export/` is written by the export tool only, never by hand.

## Publishing

Staff publish from the page at https://int.solagratia.com.au/rosart/ (Check, then Publish; it runs on the office box). Point
staff there: they do not run anything on their PC.

The project skill `.claude/skills/publish/SKILL.md` ("publish the website") is the owner's
fallback, used when the box or the page is down and never while a box job is running. It runs
`make doctor` (preflight), `make publish-dryrun` and `make changes` (preview, then the owner
confirms), `make publish-only PUBLISH_FLAGS="--yes --wait"`, then `make publish-commit` (commits
`export/` only, locally). One-time PC setup: `docs/staff-setup.md`.

## Rules for sessions that publish (the owner's fallback path; staff use the page)

- Change content in Shopify only. Never edit code, tests, the theme-fidelity allowlist
  (`src/lib/allowed-differences.ts`), `deploy.config.json`, the Makefile or `export/` to make a
  check pass: explain the failure, point to the Shopify fix, or send it to the owner.
- Never skip checks, never use Shopify write scopes or `--allow-mutations`, never run
  `make seo-apply` or `tools/seo_apply.py`.
- Never `git push`: the owner pushes. Commit only through `make publish-commit`.
- Never ask for, paste, print or read keys (the AWS deploy key lives only in each person's
  `rosart-deploy` AWS CLI profile; never print `~/.aws/credentials`).

Code changes (site, tools, tests, allowlist, config) are the owner's, test-first, with the
README updated in the same change.

## Commands

```sh
make doctor           # publish preflight (read-only)
make test             # Python tests: export, publish, doctor, change summary (no network)
make check            # build, site tests, type check, dist check
```

On Windows use `python`, never `python3`.
