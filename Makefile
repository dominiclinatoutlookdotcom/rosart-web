PYTHON ?= python
NPM ?= npm
# Extra flags for tools/publish.py, e.g. `make publish PUBLISH_FLAGS="--yes --wait"`.
PUBLISH_FLAGS ?=
DOCKER ?= docker
HARBOR ?= harbor.int.solagratia.com.au
# The V-0 probe image (sgs-server spec 006, plan Phase 0), tagged with the commit it was built from.
PROBE_IMAGE ?= $(HARBOR)/demo/rosart-publisher-probe
PROBE_TAG ?= $(shell git rev-parse --short HEAD 2>/dev/null)
# The service image (spec 006 T098): `make image VERSION=v1.0.0`.
IMAGE ?= $(HARBOR)/demo/rosart-publisher
TEST_IMAGE ?= rosart-publisher-test:local
SELFTEST_IMAGE ?= rosart-publisher-selftest:local

# Astro's anonymous usage telemetry is an outbound call on every build; keep builds offline.
export ASTRO_TELEMETRY_DISABLED := 1

.PHONY: theme-pull export export-json test site check dev doctor changes publish-config publish publish-only publish-dryrun publish-commit seo-apply-dryrun seo-apply image-probe image image-test

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

## Build, then site tests (the theme fidelity tests read the fresh dist/), type check, and check
## dist/ (pages, links, theme fidelity, no store references, image sizes)
check:
	$(NPM) run build
	$(NPM) test
	$(NPM) run typecheck
	$(NPM) run check:dist

## Local development server with live reload (http://localhost:4321/)
dev:
	$(NPM) run dev

## Publish preflight: tools, repo state, deploy config, AWS profile, Shopify sign-in (changes nothing)
doctor:
	$(PYTHON) tools/publish_doctor.py

## What the current export/ changes since the last commit, in plain words, with URLs to spot-check
changes:
	$(PYTHON) tools/export_changes.py

## After a successful publish: commit export/ only as "Publish <date> by <git user.name>"; never pushes
publish-commit:
	$(PYTHON) tools/export_changes.py --commit

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

# Refuse (calling nothing) while a pinned-by-digest ARG in the Dockerfile is not 64 hex characters.
# $(1) names the target for the message, $(2) the ARGs.
define require-pins
for arg in $(2); do \
  tr -d '\r' < Dockerfile | grep -Eq "^ARG $$arg=[0-9a-f]{64}$$" || { \
    echo "$(1): ARG $$arg in Dockerfile is not a sha256 digest yet; pin it in a reviewed commit (spec 006 T001, T098)"; \
    exit 1; }; \
done
endef

## V-0 CPU probe image (sgs-server spec 006, plan Phase 0): build the Dockerfile's probe stage for
## linux/amd64, push it to Harbor demo/ and print its digest and the box command. Refuses until the
## frozen base digests from owner task T001 are in the Dockerfile.
image-probe:
	@$(call require-pins,image-probe,NODE_SHA256 PYTHON_SHA256)
	@test -n "$(PROBE_TAG)" || { echo "image-probe: no PROBE_TAG (not a git checkout?)"; exit 1; }
	$(DOCKER) buildx build --platform linux/amd64 --target probe -t $(PROBE_IMAGE):$(PROBE_TAG) --push .
	@digest=$$($(DOCKER) buildx imagetools inspect $(PROBE_IMAGE):$(PROBE_TAG) | awk '/^Digest:/ {print $$2; exit}'); \
	test -n "$$digest" || { echo "image-probe: could not read the pushed digest"; exit 1; }; \
	echo "Probe digest: $$digest"; \
	echo "V-0 on the box (owner's go-ahead): docker run --rm --network none --read-only --tmpfs /tmp:size=2g,exec --user 10001:10001 $(PROBE_IMAGE)@$$digest"

## The service image (sgs-server spec 006, R-5): make image VERSION=v1.0.0. Refuses a malformed VERSION, a
## dirty tree (untracked files included) and unpinned digests; builds the final stage for linux/amd64,
## pushes harbor/demo/rosart-publisher:<VERSION> and prints the index digest and the commit it was built from.
## The digest reaches sgs-server compose only in a reviewed digest-bump commit.
image:
	@echo "$(VERSION)" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$$' || { echo "image: VERSION=vX.Y.Z is required (for example make image VERSION=v1.0.0)"; exit 1; }
	@test -z "$$(git status --porcelain)" || { echo "image: the working tree is not clean (commit or remove your changes first); an image is built from a reviewed commit"; exit 1; }
	@$(call require-pins,image,NODE_SHA256 PYTHON_SHA256 AWSCLI_SHA256)
	@commit=$$(git rev-parse HEAD); \
	$(DOCKER) buildx build --platform linux/amd64 --target final --build-arg PUBLISHER_CODE_COMMIT=$$commit -t $(IMAGE):$(VERSION) --push . || exit 1; \
	digest=$$($(DOCKER) buildx imagetools inspect $(IMAGE):$(VERSION) | awk '/^Digest:/ {print $$2; exit}'); \
	test -n "$$digest" || { echo "image: could not read the pushed digest"; exit 1; }; \
	echo "Image digest: $$digest"; \
	echo "rosart-web commit: $$commit"; \
	echo "Compose image line: $(IMAGE):$(VERSION)@$$digest"

## The whole suite inside the image's test stage (POSIX-only tests must run, none skipped), then the
## service's own selftest in the final image. Local build, never pushes.
image-test:
	$(DOCKER) build --platform linux/amd64 --target test -t $(TEST_IMAGE) .
	$(DOCKER) run --rm --tmpfs /tmp:size=2g,exec $(TEST_IMAGE)
	$(DOCKER) build --platform linux/amd64 --target final -t $(SELFTEST_IMAGE) .
	$(DOCKER) run --rm --read-only --tmpfs /tmp:size=512m,mode=1777,exec --user 10001:10001 $(SELFTEST_IMAGE) python -m publisher selftest
