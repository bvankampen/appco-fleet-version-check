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

The script requires **Python 3**. 

To make running the script seamless, **all third-party Python dependencies (including `requests`, `PyYAML`, and `ruamel.yaml`) are automatically installed and managed inside a local virtual environment (`.venv`) on the first run.** You do not need to install any Python packages manually.

To extract and check container images used inside Helm charts, the **Helm CLI** (`helm`) must also be installed and available in your `PATH`. If Helm is not installed, the script will gracefully skip the image checks and only verify Helm chart versions.

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

**Example Output:**

```text
[INFO] Scanning fleet directory: /path/to/your/fleet
[INFO] Using Appco API: https://api.apps.rancher.io
[INFO] Found 2 Appco application bundles. Verifying versions...
-------------------------------------------------------------------------------------------------------------
Application          Type       Artifact Name             Local Version      Latest Appco       Status         
-------------------------------------------------------------------------------------------------------------
vault                Chart      vault                     0.34.0             1.2.0-5.6          OUTDATED       
                     Image        └─ vault                1.14.0             1.14.2             OUTDATED       
postgresql           Chart      postgresql                14.2.0             14.2.0             UP-TO-DATE     
                     Image        └─ postgresql           14.2.0             14.2.0             UP-TO-DATE     
-------------------------------------------------------------------------------------------------------------
[INFO] 1 application chart(s) have new versions available.
[INFO] 1 container image(s) have new versions available.

Run with the '--apply' flag to automatically update the fleet.yaml files.
Run with the '--apply-images' flag to automatically update the image tags in the fleet.yaml files.
```

### 2. Automatically Apply Chart Updates

```bash
python3 check_versions.py -d /path/to/your/fleet --apply
```

**Example Output:**

```text
[INFO] Scanning fleet directory: /path/to/your/fleet
[INFO] Using Appco API: https://api.apps.rancher.io
[INFO] Found 2 Appco application bundles. Verifying versions...
-------------------------------------------------------------------------------------------------------------
Application          Type       Artifact Name             Local Version      Latest Appco       Status         
-------------------------------------------------------------------------------------------------------------
vault                Chart      vault                     0.34.0             1.2.0-5.6          OUTDATED       
                     Image        └─ vault                1.14.0             1.14.2             OUTDATED       
postgresql           Chart      postgresql                14.2.0             14.2.0             UP-TO-DATE     
                     Image        └─ postgresql           14.2.0             14.2.0             UP-TO-DATE     
-------------------------------------------------------------------------------------------------------------
[INFO] 1 application chart(s) have new versions available.
[INFO] 1 container image(s) have new versions available.

Applying version updates...
[SUCCESS] Updated vault to version 1.2.0-5.6 in /path/to/your/fleet/vault/fleet.yaml
[SUCCESS] Successfully updated 1 application bundle(s).
```

### 3. Automatically Apply Image Tag Updates

```bash
python3 check_versions.py -d /path/to/your/fleet --apply-images
```

**Example Output:**

```text
[INFO] Scanning fleet directory: /path/to/your/fleet
[INFO] Using Appco API: https://api.apps.rancher.io
[INFO] Found 2 Appco application bundles. Verifying versions...
-------------------------------------------------------------------------------------------------------------
Application          Type       Artifact Name             Local Version      Latest Appco       Status         
-------------------------------------------------------------------------------------------------------------
vault                Chart      vault                     0.34.0             1.2.0-5.6          OUTDATED       
                     Image        └─ vault                1.14.0             1.14.2             OUTDATED       
postgresql           Chart      postgresql                14.2.0             14.2.0             UP-TO-DATE     
                     Image        └─ postgresql           14.2.0             14.2.0             UP-TO-DATE     
-------------------------------------------------------------------------------------------------------------
[INFO] 1 application chart(s) have new versions available.
[INFO] 1 container image(s) have new versions available.

Applying image updates...
[SUCCESS] Updated image vault tag from 1.14.0 to 1.14.2 in /path/to/your/fleet/vault/fleet.yaml
[SUCCESS] Successfully updated 1 image tag(s).
```

### 4. Check or Apply Updates to a Single Application

To scan or update only a single application (e.g. `postgresql`):

```bash
python3 check_versions.py -d /path/to/your/fleet --apply --app postgresql
```

### 5. CLI Arguments Reference

- `-d`, `--fleet-dir`: Path to the fleet bundles folder.
- `-c`, `--config`: Path to the config file (default: `config.yaml`).
- `--apply`: Apply version changes directly to `fleet.yaml` files.
- `--apply-images`: Apply image tag changes directly to the `fleet.yaml` files.
- `-y`, `--yes`: Automatic yes to prompts; assumes 'yes' to all confirmation prompts during image tag override injection.
- `--dry-run`: Run in dry-run mode. Simulated updates will be printed, but no files will actually be modified.
- `--app <app-name>`: Scope actions to a single application folder.
- `--verbose`: Enable detailed request logging for debugging.
- `--debug`: Enable detailed magenta-colored debug logging.

---

## Running Unit Tests

To run the self-contained suite of unit tests verifying version comparisons, scanning, and file modification logic:

```bash
python3 test_check_versions.py
```
