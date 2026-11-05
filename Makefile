PYTHON ?= python
NPM ?= npm

# Astro's anonymous usage telemetry is an outbound call on every build; keep builds offline.
export ASTRO_TELEMETRY_DISABLED := 1

.PHONY: export export-json test site check dev

## Export store content (JSON), theme copy and every referenced image into export/
export:
	$(PYTHON) tools/export_shopify.py

## Same, but write the JSON only and download no images
export-json:
	$(PYTHON) tools/export_shopify.py --skip-images

## Run the export tool's tests (no network, no Shopify)
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
