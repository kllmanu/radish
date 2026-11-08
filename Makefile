PYTHON ?= python3
PIP := $(PYTHON) -m pip

.PHONY: install test build

install:
	$(PIP) install -e .

test:
	$(PYTHON) -m unittest discover -s tests -v

build:
	$(PIP) install -e '.[binary]'
	$(PYTHON) -m PyInstaller --clean --noconfirm --onefile \
		--name radish --specpath build --collect-submodules mutagen radish.py
