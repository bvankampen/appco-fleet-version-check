# Rancher Prime Application Collection Version Checker for Fleet Bundles

A handy command-line tool written in Python 3 to automatically scan your Rancher Fleet application bundles, detect which applications are sourced from the Rancher Prime Application Collection (Appco), query the Appco API for updates, and optionally update your files.

---

## Key Features

- **Automated Scanning**: Recursively scans a local Git repository/directory for `fleet.yaml` configurations.
- **Appco Application Recognition**: Automatically identifies Appco applications by checking if they are pulled from `dp.apps.rancher.io` or any mirror repositories (like `registry.lab.suse/dp.apps.rancher.io`).
- **Public API Querying**: Queries the public Rancher Appco Metadata API without requiring any credentials or authentication.
- **Smart Style Auto-detection**: Automatically matches the existing formatting in your files:
  - If you use plain versions (e.g., `1.20.0`), it will update to the latest plain version (e.g., `1.21.1`).
  - If you include revision tags (e.g., `1.20.0-1.1`), it will update to the latest full tag containing the revision suffix (e.g., `1.21.1-2.1`).
- **Surgical Updates**: Rather than rewriting and cluttering your `fleet.yaml` files, the script uses precise pattern matching to surgically swap only the `version` line, preserving all existing structure, indentation, whitespace, and comments.

---

## Prerequisites

The script requires Python 3 with the `requests` and `PyYAML` libraries installed:

```bash
pip install requests pyyaml
```

---

## Configuration (`config.yaml`)

The script can be customized using a local `config.yaml` file. If not specified, default values are used:

```yaml
# Path to the fleet directory containing application bundles.
# Required if you do not pass it via the command line.
fleet_dir: null

# Base URL for the Rancher Prime Application Collection Metadata API
api_url: "https://api.apps.rancher.io"

# List of domains/patterns used to recognize an Appco application
appco_domains:
  - "dp.apps.rancher.io"

# Default version update behavior:
# - "auto": detect and match existing file format (recommended)
# - "plain": write plain version numbers, e.g., "1.21.1"
# - "full": write full version strings with revision, e.g., "1.21.1-2.1"
version_format: "auto"
```

---

## How to Use

By default, the script runs in **dry-run** mode. It will only verify versions and report status without altering any files.

### 1. Check Version Status (Dry-Run / Report Only)

```bash
python3 check_versions.py -d /path/to/your/fleet
```

### 2. Automatically Apply Updates to Outdated Apps

```bash
python3 check_versions.py -d /path/to/your/fleet --apply
```

### 3. Check or Apply Updates to a Single Application

To scan or update only a single application (e.g. `postgresql`):

```bash
python3 check_versions.py -d /path/to/your/fleet --apply --app postgresql
```

### 4. CLI Arguments Reference

- `-d`, `--fleet-dir`: Path to the fleet bundles folder.
- `-c`, `--config`: Path to the config file (default: `config.yaml`).
- `--apply`: Apply version changes directly to `fleet.yaml` files.
- `--app <app-name>`: Scope actions to a single application folder.
- `--verbose`: Enable detailed request logging for debugging.

---

## Running Unit Tests

To run the self-contained suite of unit tests verifying version comparisons, scanning, and file modification logic:

```bash
python3 test_check_versions.py
```
