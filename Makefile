PYTHON ?= python

.PHONY: export export-json test

## Export store content (JSON), theme copy and every referenced image into export/
export:
	$(PYTHON) tools/export_shopify.py

## Same, but write the JSON only and download no images
export-json:
	$(PYTHON) tools/export_shopify.py --skip-images

## Run the export tool's tests (no network, no Shopify)
test:
	$(PYTHON) -m pytest -q tests
