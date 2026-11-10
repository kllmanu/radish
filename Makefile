PYTHON ?= python3
PIP := $(PYTHON) -m pip
LOCAL_BIN ?= $(HOME)/.local/bin
VIM_PLUGIN_DIR ?= $(HOME)/.vim/plugin
BASH_COMPLETION_DIR ?= $(HOME)/.local/share/bash-completion/completions

.PHONY: install test build

install:
	$(PIP) install -e .
	mkdir -p "$(LOCAL_BIN)"
	install -m 755 contrib/bin/* "$(LOCAL_BIN)/"
	mkdir -p "$(BASH_COMPLETION_DIR)"
	install -m 644 contrib/bash-completion/radish "$(BASH_COMPLETION_DIR)/radish"
	mkdir -p "$(VIM_PLUGIN_DIR)"
	install -m 644 contrib/vim/utils.vim "$(VIM_PLUGIN_DIR)/"

test:
	$(PYTHON) -m unittest discover -s tests -v

build:
	$(PIP) install -e '.[binary]'
	$(PYTHON) -m PyInstaller --clean --noconfirm --onefile \
		--name radish --specpath build --collect-submodules mutagen radish.py
