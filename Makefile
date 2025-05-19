# File Synchronizer Makefile

# Configuration variables
PYTHON := python3
CYTHON := cython
CC := gcc
STRIP := strip

# Detect OS
UNAME := $(shell uname)

# Detect Python configuration
PY_VERSION := $(shell $(PYTHON) -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")

# Linux specific flags
CFLAGS := -I/usr/include/python$(PY_VERSION)
LDFLAGS := -lpython$(PY_VERSION) -lutil -lm -ldl
INSTALL_DIR := /usr/local/bin

# Executable name
EXECUTABLE := syncer

# Source and build files
PY_SOURCE := syncer.py
C_SOURCE := syncer.c
BUILD_DIR := build

# Main targets
.PHONY: all clean

all: $(BUILD_DIR)/$(EXECUTABLE)

# Create build directory
$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

# Cython: Python to C
$(BUILD_DIR)/$(C_SOURCE): $(PY_SOURCE) | $(BUILD_DIR)
	$(CYTHON) --embed -o $@ $<

# GCC: C to executable
$(BUILD_DIR)/$(EXECUTABLE): $(BUILD_DIR)/$(C_SOURCE)
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)
	$(STRIP) $@

# Clean build artifacts
clean:
	@echo "Cleaning build artifacts..."
	@rm -rf $(BUILD_DIR)
	@echo "Cleaning complete."

# Print configuration
config:
	@echo "Python version: $(PY_VERSION)"
	@echo "CFLAGS: $(CFLAGS)"
	@echo "LDFLAGS: $(LDFLAGS)"
	@echo "Install directory: $(INSTALL_DIR)"

# Help command
help:
	@echo "File Synchronizer Makefile"
	@echo ""
	@echo "Available targets:"
	@echo "  all        - Build the executable (default)"
	@echo "  clean      - Remove all build artifacts"
	@echo "  install    - Install the executable to $(INSTALL_DIR)"
	@echo "  uninstall  - Remove the installed executable"
	@echo "  config     - Show build configuration"
