ROUTER_PATH ?= /Users/mrinal/free-llm-router/python
PYTHON ?= python3

.PHONY: install engine server build typecheck test vendor-router help

help:
	@echo "install        install engine (pip) + server (npm) deps"
	@echo "engine         run the Python evidence engine (FastAPI on :8723)"
	@echo "server         run the TypeScript MCP server (dev, talks to engine)"
	@echo "build          compile the TS server to dist/"
	@echo "typecheck      tsc --noEmit on the server"
	@echo "test           engine pytest + server typecheck"
	@echo "vendor-router  copy free-llm-router python twin into engine/vendor (for Docker)"

install:
	cd engine && pip install -r requirements.txt
	cd server && npm install

engine:
	cd engine && GROUNDCHECK_ROUTER_PATH=$(ROUTER_PATH) $(PYTHON) -m groundcheck_engine

server:
	cd server && npm run dev

build:
	cd server && npm run build

typecheck:
	cd server && npm run typecheck

test:
	cd engine && GROUNDCHECK_ROUTER_PATH=$(ROUTER_PATH) $(PYTHON) -m pytest -q
	cd server && npm run typecheck

vendor-router:
	rm -rf engine/vendor/free_llm_router
	mkdir -p engine/vendor
	cp -R $(ROUTER_PATH)/free_llm_router engine/vendor/free_llm_router
	@echo "vendored free_llm_router from $(ROUTER_PATH)"
