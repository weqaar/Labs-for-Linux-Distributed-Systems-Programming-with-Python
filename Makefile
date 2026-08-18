.PHONY: labs local-check local-up local-down

labs:
	./scripts/check-labs.sh

local-check:
	docker compose --file local/compose.yaml config --quiet

local-up:
	docker compose --file local/compose.yaml up --detach

local-down:
	docker compose --file local/compose.yaml down
