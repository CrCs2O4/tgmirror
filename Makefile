CONFIG   ?= config.toml
DATA_DIR ?= $(PWD)
IMAGE    ?= tgmirror

.PHONY: help # List all documented targets
help:
	@grep '^.PHONY: .* #' Makefile | sed 's/\.PHONY: \(.*\) # \(.*\)/\1\t\2/' | sort | expand -t20

.PHONY: setup # Install uv (if missing) and sync all dependencies
setup:
	@command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh
	uv sync --all-groups

.PHONY: install # Sync dependencies into .venv via uv (requires uv)
install:
	uv sync --all-groups

.PHONY: run # Run the forwarder — launches setup wizard if config.toml is missing
run:
	uv run python main.py $(CONFIG)

.PHONY: debug # Run the forwarder with DEBUG logging
debug:
	LOG_LEVEL=DEBUG uv run python main.py $(CONFIG)

.PHONY: wizard # Run the interactive setup wizard (re-configure config.toml)
wizard:
	uv run python setup.py

.PHONY: test # Run tests
test:
	uv run pytest tests/ -v

.PHONY: lint # Run ruff linter
lint:
	uv run ruff check .

.PHONY: fmt # Format code with ruff
fmt:
	uv run ruff format .

.PHONY: docker-build # Build Docker image
docker-build:
	docker build -t $(IMAGE) .

# -it keeps stdin open for the Pyrogram auth prompt on first run.
# Mount DATA_DIR to /data — place config.toml there; session + state persist there.
.PHONY: docker-run # Run via Docker, interactive (DATA_DIR=<dir>, IMAGE=<name>)
docker-run:
	docker run -it --rm \
		-v "$(DATA_DIR):/data" \
		$(IMAGE)
