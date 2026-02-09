# TextQL - build and run
# Usage: make dev | make build | make run

.PHONY: build dev run

# Build Docker sandbox + install all deps
build:
	@echo "==> Building Docker sandbox image..."
	docker build -t textql-sandbox:py311 backend/sandbox/runner
	@echo "==> Installing backend dependencies..."
	cd backend && uv sync
	@echo "==> Installing frontend dependencies..."
	cd frontend && npm install
	@echo "==> Build complete. Run 'make run' to start."

# Full build + run backend and frontend
dev: build
	@./scripts/dev.sh --no-build

# Run only (skips build; use after 'make build')
run:
	@./scripts/dev.sh --no-build
