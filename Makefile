# Cx-Preflight Edge
# PC(Windows Git Bash / Linux)와 보드(aarch64 Ubuntu) 공용.
PY ?= $(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python,.venv/bin/python)

.PHONY: venv test serve synth demo lint

venv:
	python -m venv .venv
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest

serve:
	$(PY) -m uvicorn cxpe.server:app --host $${CXPE_HOST:-127.0.0.1} --port $${CXPE_PORT:-8080}

synth:
	$(PY) -m cxpe.cli synth --case pass --out sessions/_synth_pass.csv

demo:
	$(PY) -m cxpe.cli replay --plan data/golden/plan.json --tags data/golden/tags.json --case fail_start

lint:
	$(PY) -m pyflakes cxpe tests || true
