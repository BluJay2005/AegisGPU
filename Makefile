.PHONY: install install-gpu demo test bench db-up load ask
PY ?= python3
export PYTHONPATH := src:.

install:      ; $(PY) -m pip install -r requirements.txt
install-gpu:  ; $(PY) -m pip install -r requirements.txt -r requirements-gpu.txt
demo:         ; $(PY) -m amlnet demo                       # zero-setup, in-memory SQLite store
test:         ; $(PY) -m pytest -q
bench:        ; $(PY) -m amlnet bench                      # AML_BACKEND=gpu make bench  on a GPU box
db-up:        ; docker compose up -d postgres
load:         ; $(PY) -m amlnet generate && $(PY) -m amlnet load   # uses DATABASE_URL if set
ask:          ; $(PY) -m amlnet ask
