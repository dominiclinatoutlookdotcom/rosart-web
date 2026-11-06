PYTHON ?= python
NPM ?= npm
# Extra flags for tools/publish.py, e.g. `make publish PUBLISH_FLAGS="--yes --wait"`.
PUBLISH_FLAGS ?=

# Astro's anonymous usage telemetry is an outbound call on every build; keep builds offline.
export ASTRO_TELEMETRY_DISABLED := 1

.PHONY: theme-pull export export-json test site check dev publish-config publish publish-only publish-dryrun seo-apply-dryrun seo-apply

## Refresh export/theme/ from the live theme (read-only `shopify theme pull`)
theme-pull:
	$(PYTHON) tools/pull_theme.py

## Pull the theme, then export store content (JSON), theme copy and every referenced image into export/
export: theme-pull
	$(PYTHON) tools/export_shopify.py

## Same, but write the JSON only and download no images
export-json: theme-pull
	$(PYTHON) tools/export_shopify.py --skip-images

## Run the Python tests: export, theme pull and publish tools (no network, no Shopify, no AWS)
test:
	$(PYTHON) -m pytest -q tests

## Install the locked dependencies and build the static site into dist/
site:
	$(NPM) ci
	$(NPM) run build

## Site tests, type check, build, then check dist/ (pages, links, no store references, image sizes)
check:
	$(NPM) test
	$(NPM) run typecheck
	$(NPM) run build
	$(NPM) run check:dist

## Local development server with live reload (http://localhost:4321/)
dev:
	$(NPM) run dev

## Refuse early while deploy.config.json still holds placeholders (calls nothing)
publish-config:
	$(PYTHON) tools/publish.py --check-config

## Export from Shopify, check, dry-run, confirm, then sync dist/ to S3 and invalidate CloudFront
publish: publish-config
	$(MAKE) export
	$(MAKE) check
	$(PYTHON) tools/publish.py $(PUBLISH_FLAGS)

## Same without the export: check the current export/ and publish it
publish-only: publish-config
	$(MAKE) check
	$(PYTHON) tools/publish.py $(PUBLISH_FLAGS)

## Export, check and show the dry run; never uploads or invalidates
publish-dryrun: publish-config
	$(MAKE) export
	$(MAKE) check
	$(PYTHON) tools/publish.py --dry-run

## SEO plan dry run: hash, seo_check, scope and drift checks (reads the store, writes nothing).
## Pass APPROVED=<sha256> to also check the hash the owner approved.
seo-apply-dryrun:
	$(PYTHON) tools/seo_apply.py --plan seo/plan.json $(if $(APPROVED),--approved $(APPROVED))

## Apply the approved SEO plan in batches with a rollback log: make seo-apply APPROVED=<sha256 of seo/plan.json>
seo-apply:
	@test -n "$(APPROVED)" || { echo "make seo-apply needs APPROVED=<sha256 of seo/plan.json>"; exit 1; }
	$(PYTHON) tools/seo_apply.py --plan seo/plan.json --approved $(APPROVED) --write
