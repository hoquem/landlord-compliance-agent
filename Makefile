# Development runner. `make` on its own lists the targets.
#
# Every recipe `cd`s where it needs to be, which removes the trap CLAUDE.md
# warns about: from the repo root a bare `uv run` finds no project and
# silently falls back to ambient Anaconda tooling. Run these from the root.
#
# `--env-file ../.env` is not optional for anything Python. It carries
# DATABASE_URL and the Supabase keys, OTEL_SDK_DISABLED (without which the
# worker refuses to start -- see src/worker/jobs.py), and on macOS
# DYLD_FALLBACK_LIBRARY_PATH (without which importing WeasyPrint, and so the
# whole API, raises OSError).
#
# `just` would be tidier, but it is not installed here and `make` is, so this
# adds no dependency.

.DEFAULT_GOAL := help
.PHONY: help dev stack down api worker web web-test web-lint test lint reset

help:  ## List available targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-8s\033[0m %s\n", $$1, $$2}'

stack:  ## Start the local Supabase stack (idempotent)
	supabase start

down:  ## Stop the local Supabase stack
	supabase stop

api:  ## Run the API on :8000 with reload
	cd backend && uv run --env-file ../.env uvicorn src.api.main:app --reload --port 8000

worker:  ## Run the job-queue worker
	cd backend && uv run --env-file ../.env python -m src.worker.main

# SUPABASE_URL / SUPABASE_ANON_KEY reach Flutter as --dart-define values,
# read from .env so there is one source. main.dart throws if either is
# missing rather than running half-configured.
#
# --web-port 3000 is not cosmetic: supabase/config.toml allowlists
# http://localhost:3000 and http://127.0.0.1:3000 as OAuth redirect targets,
# and Google refuses any other origin. On a random port, sign-in fails with
# a provider error that says nothing about ports.
FLUTTER_DEFINES = \
	--web-port 3000 \
	--dart-define=SUPABASE_URL=$(shell grep '^SUPABASE_URL=' .env | cut -d= -f2-) \
	--dart-define=SUPABASE_ANON_KEY=$(shell grep '^SUPABASE_ANON_KEY=' .env | cut -d= -f2-)

web:  ## Run the Flutter app in Chrome on :3000
	cd frontend && flutter run -d chrome $(FLUTTER_DEFINES)

web-test:  ## Run the Flutter widget tests
	cd frontend && flutter test

web-lint:  ## Analyze the Flutter package
	cd frontend && flutter analyze

test:  ## Run the backend suite (needs the stack up)
	cd backend && uv run --env-file ../.env pytest

lint:  ## Ruff, the project's only lint gate
	cd backend && uv run ruff check src tests

reset:  ## Re-apply all migrations from scratch -- DESTROYS local data
	supabase db reset

dev: stack  ## Stack + API + worker + Flutter, all together; Ctrl-C stops all
#	One shell for the whole recipe (note the trailing backslashes), because
#	the trap has to outlive the individual commands. `kill 0` signals the
#	whole process group, so the children go with the parent.
#
#	Measured with three `sleep`s standing in for the real processes, killing
#	make itself (the orphaning case -- a plain Ctrl-C also reaches the
#	children directly, so it does not discriminate): WITHOUT the trap, two
#	children survived; WITH it, zero. Those orphans would be a worker
#	polling the database forever and a uvicorn holding :8000, so the next
#	`make dev` fails on a port nothing visible is using.
	@trap 'kill 0' EXIT INT TERM; \
	( cd backend && uv run --env-file ../.env uvicorn src.api.main:app --reload --port 8000 ) & \
	( cd backend && uv run --env-file ../.env python -m src.worker.main ) & \
	( cd frontend && flutter run -d chrome $(FLUTTER_DEFINES) ) & \
	wait
