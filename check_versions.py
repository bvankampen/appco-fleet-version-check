#!/usr/bin/env python3
"""
Rancher Prime Application Collection (Appco) Version Checker
Checks local Fleet application bundles against the latest versions in the Appco registry.
"""

import os
import sys
import subprocess

def bootstrap_dependencies():
    """
    Checks if required dependencies can be imported.
    If missing, automatically creates a local virtual environment (.venv)
    next to the script, installs dependencies, and re-executes itself.
    """
    required_packages = ["requests", "yaml", "ruamel.yaml"]
    missing = False
    for pkg in required_packages:
        try:
            __import__(pkg)
        except ImportError:
            missing = True
            break
            
    if not missing:
        return # All good!
        
    script_dir = os.path.dirname(os.path.abspath(__file__))
    venv_dir = os.path.join(script_dir, ".venv")
    is_in_venv = sys.prefix != sys.base_prefix
    
    if is_in_venv:
        # If we are already in our venv but still missing dependencies, install them
        print("Required dependencies missing inside virtual environment. Installing...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "requests", "pyyaml", "ruamel.yaml>=0.17"])
            # Validate imports
            for pkg in required_packages:
                __import__(pkg)
            return
        except Exception as e:
            print(f"[ERROR] Failed to install dependencies in active venv: {e}", file=sys.stderr)
            sys.exit(1)
            
    # Not in venv. Create it if missing
    if not os.path.exists(venv_dir):
        print(f"Required dependencies missing. Creating virtual environment in {venv_dir}...")
        try:
            subprocess.check_call([sys.executable, "-m", "venv", venv_dir])
            print("Installing python dependencies (requests, pyyaml, ruamel.yaml) inside .venv...")
            # Use venv pip to install
            pip_exe = os.path.join(venv_dir, "bin", "pip")
            subprocess.check_call([pip_exe, "install", "--quiet", "--upgrade", "pip"])
            subprocess.check_call([pip_exe, "install", "--quiet", "requests", "pyyaml", "ruamel.yaml>=0.17"])
        except Exception as e:
            print(f"[ERROR] Failed to bootstrap virtual environment: {e}", file=sys.stderr)
            sys.exit(1)
            
    # Re-execute the script using the venv python interpreter
    venv_python = os.path.join(venv_dir, "bin", "python3")
    if not os.path.exists(venv_python):
        venv_python = os.path.join(venv_dir, "Scripts", "python.exe")
        
    if os.path.exists(venv_python):
        os.execv(venv_python, [venv_python] + sys.argv)
    else:
        print("[ERROR] Virtual environment python binary not found.", file=sys.stderr)
        sys.exit(1)

# Ensure dependencies are available before importing them
bootstrap_dependencies()

import re
import yaml
import argparse
import requests
import tempfile
import shutil

# ANSI Color codes for clean terminal output
COLOR_RESET = "\033[0m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_RED = "\033[91m"
COLOR_BLUE = "\033[94m"
COLOR_MAGENTA = "\033[95m"
COLOR_BOLD = "\033[1m"

# Global debug state
DEBUG_MODE = False

def log_info(msg):
    print(f"{COLOR_BLUE}[INFO]{COLOR_RESET} {msg}")

def log_success(msg):
    print(f"{COLOR_GREEN}[SUCCESS]{COLOR_RESET} {msg}")

def log_warn(msg):
    print(f"{COLOR_YELLOW}[WARN]{COLOR_RESET} {msg}")

def log_error(msg):
    print(f"{COLOR_RED}[ERROR]{COLOR_RESET} {msg}", file=sys.stderr)

def log_debug(msg):
    if DEBUG_MODE:
        print(f"{COLOR_MAGENTA}[DEBUG]{COLOR_RESET} {msg}")

def check_helm_installed():
    """Checks if the helm CLI tool is installed and accessible."""
    try:
        subprocess.run(["helm", "version"], capture_output=True, check=True)
        return True
    except Exception:
        return False

def get_helm_template_images(chart_url, chart_version, values_dict, verbose=False):
    """
    Downloads a helm chart using 'helm pull --untar', applies values_dict if provided,
    runs 'helm template', and recursively parses the output to extract all image references.
    """
    images = set()
    
    # Create a temporary directory
    with tempfile.TemporaryDirectory() as temp_dir:
        # Resolve chart name from URL (last component)
        # E.g. oci://registry.lab.suse/dp.apps.rancher.io/charts/vault -> vault
        chart_name = chart_url.split("/")[-1].split(":")[0].split("@")[0]
        
        # Run 'helm pull'
        pull_cmd = ["helm", "pull", chart_url, "--version", chart_version, "--untar", "-d", temp_dir]
        log_debug(f"Running command: {' '.join(pull_cmd)}")
            
        try:
            subprocess.run(pull_cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            log_error(f"Failed to pull helm chart {chart_url} (version {chart_version}): {e.stderr}")
            return []
            
        # The untarred chart should be at temp_dir/chart_name
        untarred_chart_path = os.path.join(temp_dir, chart_name)
        if not os.path.exists(untarred_chart_path):
            # Sometimes chart_name can differ from the folder name in OCI registry
            # List directory to find the actual folder
            try:
                entries = os.listdir(temp_dir)
                if entries:
                    untarred_chart_path = os.path.join(temp_dir, entries[0])
                    log_debug(f"Resolved untarred chart path to {untarred_chart_path}")
                else:
                    log_error(f"Untarred chart directory not found in {temp_dir}")
                    return []
            except Exception as e:
                log_error(f"Error accessing temp directory: {e}")
                return []
                
        # Create temp values file if values_dict is provided
        values_file_path = None
        if values_dict:
            values_file_path = os.path.join(temp_dir, "temp_values.yaml")
            try:
                log_debug(f"Writing temp values.yaml to {values_file_path}")
                with open(values_file_path, 'w') as f:
                    yaml.dump(values_dict, f)
            except Exception as e:
                log_error(f"Failed to write temp values.yaml: {e}")
                
        # Run 'helm template'
        template_cmd = ["helm", "template", "appco-release", untarred_chart_path]
        if values_file_path:
            template_cmd.extend(["-f", values_file_path])
            
        log_debug(f"Running command: {' '.join(template_cmd)}")
            
        try:
            res = subprocess.run(template_cmd, capture_output=True, text=True, check=True)
            template_output = res.stdout
        except subprocess.CalledProcessError as e:
            log_error(f"Failed to render helm template for {chart_name}: {e.stderr}")
            return []
            
        # Parse templates and extract image references
        try:
            log_debug("Parsing rendered helm manifests with PyYAML")
            manifests = yaml.safe_load_all(template_output)
            
            def find_images(node):
                if isinstance(node, dict):
                    for k, v in node.items():
                        if k == "image" and isinstance(v, str):
                            images.add(v)
                        else:
                            find_images(v)
                elif isinstance(node, list):
                    for item in node:
                        find_images(item)
                        
            for manifest in manifests:
                if manifest: # safe_load_all can return None elements
                    find_images(manifest)
        except Exception as e:
            log_warn(f"Failed to parse rendered helm manifests with PyYAML ({e}). Using regex fallback.")
            
        # Fallback to regex if yaml parsing failed or extracted nothing
        if not images:
            log_debug("Applying regex extraction for image tags")
            pattern = re.compile(r'image:\s*"?([^"\s]+)"?')
            for line in template_output.splitlines():
                match = pattern.search(line)
                if match:
                    images.add(match.group(1).strip())
            
        log_debug(f"Extracted images: {sorted(list(images))}")
            
    return sorted(list(images))

def surgical_update_image_tag(filepath, old_tag, new_tag):
    """
    Surgically updates the image tag inside the helm: values block of fleet.yaml.
    Preserves comments, indentation, spacing, and all other properties.
    """
    if not os.path.exists(filepath):
        return False
        
    with open(filepath, 'r') as f:
        lines = f.readlines()
        
    updated = False
    new_lines = []
    in_helm = False
    
    # Precise regex to match old_tag surrounded by typical yaml value delimiters:
    # Colons, quotes, spaces or newlines.
    pattern = re.compile(r'([\'":\s])' + re.escape(old_tag) + r'([\'"\s\n#])')
    
    for line in lines:
        stripped = line.strip()
        
        # Detect entry to helm block
        if stripped.startswith("helm:"):
            in_helm = True
            new_lines.append(line)
            continue
            
        if in_helm:
            if stripped == "":
                new_lines.append(line)
                continue
            # If line starts with spaces or tabs, it is a nested property under helm:
            elif line.startswith(" ") or line.startswith("\t"):
                if old_tag in line:
                    new_line = pattern.sub(r'\g<1>' + new_tag + r'\g<2>', line)
                    if new_line != line:
                        line = new_line
                        updated = True
            else:
                # Line does not start with spaces, indicating exit from helm block
                in_helm = False
                
        new_lines.append(line)
        
    if updated:
        with open(filepath, 'w') as f:
            f.writelines(new_lines)
            
    return updated

def get_chart_default_values(chart_url, chart_version, verbose=False):
    """
    Downloads the chart, parses its default values.yaml, and recursively
    collects and merges any subcharts' default values.yaml under their subchart names.
    """
    def load_values_recursive(chart_dir):
        # Load this chart's values.yaml
        values_yaml_path = os.path.join(chart_dir, "values.yaml")
        values = {}
        if os.path.exists(values_yaml_path):
            try:
                with open(values_yaml_path, 'r') as f:
                    values = yaml.safe_load(f) or {}
            except Exception as e:
                log_error(f"Failed to parse values.yaml in {chart_dir}: {e}")
                
        # Look for subcharts inside the 'charts/' subdirectory
        subcharts_dir = os.path.join(chart_dir, "charts")
        if os.path.exists(subcharts_dir) and os.path.isdir(subcharts_dir):
            try:
                for entry in os.listdir(subcharts_dir):
                    subchart_path = os.path.join(subcharts_dir, entry)
                    if os.path.isdir(subchart_path):
                        # Verify it is a valid chart folder (has Chart.yaml)
                        if os.path.exists(os.path.join(subchart_path, "Chart.yaml")):
                            subchart_values = load_values_recursive(subchart_path)
                            if subchart_values:
                                # Ensure we don't overwrite any explicit overrides the parent has
                                if entry not in values or not isinstance(values[entry], dict):
                                    values[entry] = subchart_values
                                else:
                                    # Merge subchart values with parent overrides recursively
                                    def deep_merge(target, source):
                                        for k, v in source.items():
                                            if k in target and isinstance(target[k], dict) and isinstance(v, dict):
                                                deep_merge(target[k], v)
                                            elif k not in target:
                                                target[k] = v
                                    deep_merge(values[entry], subchart_values)
            except Exception as e:
                log_error(f"Failed to load subcharts in {subcharts_dir}: {e}")
                
        return values

    with tempfile.TemporaryDirectory() as temp_dir:
        chart_name = chart_url.split("/")[-1].split(":")[0].split("@")[0]
        pull_cmd = ["helm", "pull", chart_url, "--version", chart_version, "--untar", "-d", temp_dir]
        if verbose:
            log_debug(f"Pulling chart default values using: {' '.join(pull_cmd)}")
            
        try:
            subprocess.run(pull_cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            log_error(f"Failed to pull helm chart default values: {e.stderr}")
            return {}
            
        untarred_chart_path = os.path.join(temp_dir, chart_name)
        if not os.path.exists(untarred_chart_path):
            try:
                entries = os.listdir(temp_dir)
                if entries:
                    untarred_chart_path = os.path.join(temp_dir, entries[0])
            except Exception:
                return {}
                
        return load_values_recursive(untarred_chart_path)
                
    return {}

def find_image_value_path(values_dict, img_repo, img_tag):
    """
    Recursively walks values_dict to find the path (list of keys) to the tag of img_repo.
    Returns (path_to_tag_parent_dict, tag_key_name) or (None, None).
    E.g. (['global', 'image'], 'tag') or (['image'], 'tag')
    """
    img_short_name = img_repo.split("/")[-1]
    
    def walk(node, current_path):
        if not isinstance(node, dict):
            return None, None
            
        # Check if the current dictionary describes this image
        has_repo = False
        has_tag = False
        repo_key = None
        tag_key = None
        
        for k, v in node.items():
            if isinstance(v, str):
                # Loose matching: check if repository matches or ends with the short name
                if k in ["repository", "image", "registry", "name"] and (v == img_repo or v.endswith(f"/{img_short_name}") or v == img_short_name):
                    has_repo = True
                    repo_key = k
                # Loose matching: check if value matches tag
                elif k in ["tag", "version"] and v == img_tag:
                    has_tag = True
                    tag_key = k
                    
        if has_repo and has_tag and tag_key:
            return current_path, tag_key
            
        # Recursive search in child dictionaries
        for k, v in node.items():
            if isinstance(v, dict):
                p, tk = walk(v, current_path + [k])
                if p is not None:
                    return p, tk
                    
        return None, None
        
    return walk(values_dict, [])

def inject_image_tag_override(filepath, value_path, tag_key, new_tag):
    """
    Safely injects or updates a nested value override under helm: values: in fleet.yaml.
    Preserves all existing comments, indentation, and structure of fleet.yaml.
    """
    try:
        from ruamel.yaml import YAML
    except ImportError:
        log_error("ruamel.yaml is not installed. Unable to safely inject YAML overrides.")
        return False
        
    if not os.path.exists(filepath):
        return False
        
    yaml_parser = YAML()
    yaml_parser.preserve_quotes = True
    yaml_parser.indent(mapping=2, sequence=4, offset=2)
    
    try:
        with open(filepath, 'r') as f:
            data = yaml_parser.load(f) or {}
    except Exception as e:
        log_error(f"Failed to parse {filepath} with ruamel.yaml: {e}")
        return False
        
    # Ensure helm: exists and is a dict
    if "helm" not in data:
        data["helm"] = {}
    elif data["helm"] is None:
        data["helm"] = {}
        
    # Ensure values: exists under helm:
    if "values" not in data["helm"]:
        data["helm"]["values"] = {}
    elif data["helm"]["values"] is None:
        data["helm"]["values"] = {}
        
    # Traverse down the heuristic value_path
    curr = data["helm"]["values"]
    for step in value_path:
        if step not in curr or not isinstance(curr[step], dict):
            curr[step] = {}
        curr = curr[step]
        
    # Update the tag value
    curr[tag_key] = new_tag
    
    # Write back
    try:
        with open(filepath, 'w') as f:
            yaml_parser.dump(data, f)
        return True
    except Exception as e:
        log_error(f"Failed to write injected updates to {filepath}: {e}")
        return False

def parse_version_key(version_str):
    """
    Parses a version string into a tuple of integers for reliable semantic sorting.
    Handles standard semver as well as revision numbers (e.g. 1.21.1-2.1).
    
    Examples:
        "1.21.1-2.1"                  -> ((1, 21, 1), (2, 1))
        "1.21.1"                      -> ((1, 21, 1), (0, 0))
        "109.0.3+up80.9.1-rancher.14" -> ((109, 0, 3), (0, 0))
    """
    if not version_str:
        return ((0, 0, 0), (0, 0))
        
    # Strip leading 'v'
    if version_str.lower().startswith('v'):
        version_str = version_str[1:]
        
    # Separate semver from revision/pre-release suffix on first hyphen
    parts = version_str.split('-', 1)
    semver_part = parts[0]
    revision_part = parts[1] if len(parts) > 1 else ""
    
    # Strip build metadata from semver part (e.g. 109.0.3+up80.9.1 -> 109.0.3)
    semver_part = semver_part.split('+', 1)[0]
    
    # Convert semver to integers
    semver_ints = []
    for x in semver_part.split('.'):
        num_str = ""
        for char in x:
            if char.isdigit():
                num_str += char
            else:
                break
        semver_ints.append(int(num_str) if num_str else 0)
        
    while len(semver_ints) < 3:
        semver_ints.append(0)
    semver_tuple = tuple(semver_ints[:3])
    
    # Parse revision part (usually of form X.Y like 18.1 or just standard int)
    revision_ints = []
    if revision_part:
        # Check if the revision part is a decimal/dot-separated revision like 18.1
        # Or split by dot to parse revision sub-versioning
        for r in revision_part.split('.'):
            num_str = ""
            for char in r:
                if char.isdigit():
                    num_str += char
                else:
                    break
            if num_str:
                revision_ints.append(int(num_str))
                
    while len(revision_ints) < 2:
        revision_ints.append(0)
    revision_tuple = tuple(revision_ints[:2])
    
    return (semver_tuple, revision_tuple)

def surgical_update_version(filepath, new_version):
    """
    Surgically updates the version key inside the helm: block of fleet.yaml.
    Preserves comments, indentation, spacing, and all other properties.
    """
    if not os.path.exists(filepath):
        return False
        
    with open(filepath, 'r') as f:
        lines = f.readlines()
        
    updated = False
    new_lines = []
    in_helm = False
    
    for line in lines:
        stripped = line.strip()
        
        # Detect entry to helm block
        if stripped.startswith("helm:"):
            in_helm = True
            new_lines.append(line)
            continue
            
        if in_helm:
            if stripped == "":
                new_lines.append(line)
                continue
            # If line starts with spaces or tabs, it is a nested property under helm:
            elif line.startswith(" ") or line.startswith("\t"):
                if stripped.startswith("version:"):
                    indent = line[:len(line) - len(line.lstrip())]
                    comment = ""
                    
                    # Safely extract line comment if any
                    if "#" in line:
                        parts = line.split("#", 1)
                        # Check that the hash is not within quotes
                        if not (parts[0].count("\"") % 2 != 0 or parts[0].count("\'") % 2 != 0):
                            comment = " #" + parts[1].rstrip()
                            
                    # Determine quotes used in the original file
                    quote = ""
                    val_part = stripped.split(":", 1)[1].strip()
                    if "#" in val_part:
                        val_part = val_part.split("#", 1)[0].strip()
                    if (val_part.startswith("'") and val_part.endswith("'")) or (val_part.startswith('"') and val_part.endswith('"')):
                        quote = val_part[0]
                        
                    new_line = f"{indent}version: {quote}{new_version}{quote}{comment}\n"
                    new_lines.append(new_line)
                    updated = True
                    continue
            else:
                # Line does not start with spaces, indicating exit from helm block
                in_helm = False
                
        new_lines.append(line)
        
    if updated:
        with open(filepath, 'w') as f:
            f.writelines(new_lines)
            
    return updated

def load_config(config_path):
    """Loads config.yaml or returns default settings."""
    defaults = {
        "fleet_dir": None,
        "api_url": "https://api.apps.rancher.io",
        "appco_domains": [
            "dp.apps.rancher.io"
        ],
        "version_format": "auto"
    }
    
    if not os.path.exists(config_path):
        return defaults
        
    try:
        with open(config_path, 'r') as f:
            user_config = yaml.safe_load(f) or {}
            
        # Merge user config into defaults
        for k, v in defaults.items():
            if k not in user_config:
                user_config[k] = v
        return user_config
    except Exception as e:
        log_warn(f"Failed to read config file {config_path}: {e}. Using defaults.")
        return defaults

def fetch_latest_appco_artifact(api_url, artifact_name, packaging_format, verbose=False):
    """
    Queries Appco API for available artifacts of specified format matching artifact_name,
    and returns the latest artifact item based on custom semver sorting.
    """
    url = f"{api_url.rstrip('/')}/v1/artifacts"
    params = {
        "packaging_formats": packaging_format,
        "name": artifact_name,
        "page_size": 100
    }
    
    log_debug(f"API Request: GET {url} with params {params}")
        
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        artifacts = data.get("items", [])
        log_debug(f"API Response: found {len(artifacts)} total artifacts")
        
        # Filter for exact name matching (artifact name must start with 'artifact_name:')
        matching_artifacts = []
        for art in artifacts:
            name = art.get("name", "")
            if name.startswith(f"{artifact_name}:"):
                matching_artifacts.append(art)
                
        log_debug(f"Matching artifacts starting with '{artifact_name}:': {[a.get('name') for a in matching_artifacts]}")
        if not matching_artifacts:
            log_debug(f"No matching artifacts starting with '{artifact_name}:' in API response.")
            return None
            
        # Sort artifacts descending by version key
        matching_artifacts.sort(
            key=lambda x: parse_version_key(x.get("name", "").split(":")[-1]),
            reverse=True
        )
        log_debug(f"Latest resolved artifact: {matching_artifacts[0].get('name')}")
        return matching_artifacts[0]
        
    except Exception as e:
        log_error(f"Failed to fetch artifacts for {artifact_name} from Appco API: {e}")
        return None

def fetch_latest_appco_version(api_url, chart_name, verbose=False):
    """
    Queries Appco API for available HELM_CHART artifacts matching chart_name,
    and returns the latest artifact item based on custom semver sorting.
    """
    return fetch_latest_appco_artifact(api_url, chart_name, "HELM_CHART", verbose)

def fetch_latest_appco_image_version(api_url, image_name, verbose=False):
    """
    Queries Appco API for available CONTAINER artifacts matching image_name,
    and returns the latest artifact item based on custom semver sorting.
    """
    return fetch_latest_appco_artifact(api_url, image_name, "CONTAINER", verbose)

def scan_fleet_directory(fleet_dir, appco_domains, target_app=None, verbose=False):
    """
    Recursively scans fleet_dir for any fleet.yaml and extracts
    Appco applications matching any of the appco_domains.
    """
    apps = []
    
    if not os.path.isdir(fleet_dir):
        log_error(f"Specified fleet directory '{fleet_dir}' is not a directory.")
        sys.exit(1)
        
    for root, _, files in os.walk(fleet_dir):
        if "fleet.yaml" in files:
            filepath = os.path.join(root, "fleet.yaml")
            log_debug(f"Scanning file: {filepath}")
            
            try:
                with open(filepath, 'r') as f:
                    data = yaml.safe_load(f) or {}
            except Exception as e:
                log_warn(f"Failed to parse {filepath}: {e}. Skipping.")
                continue
                
            helm = data.get("helm", {})
            chart = helm.get("chart") or ""
            version = str(helm.get("version", "")) if helm.get("version") is not None else ""
            values = helm.get("values", {})
            
            # Extract the raw chart name from the registry path
            # E.g. oci://registry.lab.suse/dp.apps.rancher.io/charts/vault -> vault
            chart_name = chart.split("/")[-1].split(":")[0].split("@")[0] if chart else ""
            
            # Determine application folder name relative to fleet directory
            rel_path = os.path.relpath(root, fleet_dir)
            app_folder_name = rel_path.split(os.sep)[0] if rel_path != "." else os.path.basename(root)
            
            # If the application folder name resolved to "fleet", use the more descriptive chart name
            if app_folder_name == "fleet" and chart_name:
                app_folder_name = chart_name
            
            # If target_app is specified, skip unrelated apps
            if target_app and app_folder_name != target_app:
                log_debug(f"Skipping {filepath}: folder name '{app_folder_name}' does not match target app '{target_app}'")
                continue
            
            if not chart or not version:
                log_debug(f"Skipping {filepath}: No chart or version specified under helm:")
                continue
                
            # Check if chart is from Appco domains
            is_appco = False
            for domain in appco_domains:
                if domain in chart:
                    is_appco = True
                    break
                    
            if not is_appco:
                log_debug(f"Skipping {filepath} ({app_folder_name}): Chart '{chart}' does not match Appco domain pattern.")
                continue
                
            log_debug(f"Found matching Appco app: {app_folder_name} (chart: {chart_name}, version: {version})")
            apps.append({
                "app_name": app_folder_name,
                "filepath": filepath,
                "chart": chart,
                "chart_name": chart_name,
                "local_version": version,
                "values": values
            })
            
    return apps

def main():
    global DEBUG_MODE
    parser = argparse.ArgumentParser(
        description="Check and update versions of Rancher Prime App Collection apps in Fleet bundles."
    )
    parser.add_argument(
        "-d", "--fleet-dir",
        help="Path to the local fleet application bundles directory."
    )
    parser.add_argument(
        "-c", "--config",
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply version changes directly to the fleet.yaml files."
    )
    parser.add_argument(
        "--apply-images",
        action="store_true",
        help="Apply image tag changes directly to the fleet.yaml files."
    )
    parser.add_argument(
        "--app",
        help="Check or apply changes only to a single specified application folder name."
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output logging."
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging."
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Automatic yes to prompts; assume 'yes' as answer to all confirmation prompts."
    )
    
    args = parser.parse_args()
    
    # Initialize global debug mode
    if args.debug or args.verbose:
        DEBUG_MODE = True
        
    log_debug(f"CLI arguments parsed: {args}")
    
    # Load configuration
    config = load_config(args.config)
    
    # Resolve fleet directory: CLI arg overrides config file
    fleet_dir = args.fleet_dir or config.get("fleet_dir")
    
    if not fleet_dir:
        log_error("Fleet directory path is not specified.")
        log_error("Please define 'fleet_dir' in config.yaml or pass it via -d/--fleet-dir.")
        sys.exit(1)
        
    api_url = config.get("api_url", "https://api.apps.rancher.io")
    appco_domains = config.get("appco_domains", [])
    version_format = config.get("version_format", "auto")
    
    log_info(f"Scanning fleet directory: {COLOR_BOLD}{fleet_dir}{COLOR_RESET}")
    log_info(f"Using Appco API: {COLOR_BOLD}{api_url}{COLOR_RESET}")
    
    # Scan for matching applications
    apps = scan_fleet_directory(fleet_dir, appco_domains, target_app=args.app, verbose=args.verbose)
    
    if not apps:
        if args.app:
            log_warn(f"No Appco application bundle named '{args.app}' was found in '{fleet_dir}'.")
        else:
            log_warn(f"No Appco applications were found in '{fleet_dir}'.")
        sys.exit(0)
        
    helm_installed = check_helm_installed()
    if not helm_installed:
        log_warn("Helm CLI is not installed or not in PATH. Skipping image checks.")
        
    log_info(f"Found {len(apps)} Appco application bundles. Verifying versions...")
    
    results = []
    outdated_charts = []
    outdated_images = []
    
    for app in apps:
        chart_name = app["chart_name"]
        local_version = app["local_version"]
        
        # 1. Check chart version
        latest_art = fetch_latest_appco_version(api_url, chart_name, verbose=args.debug or args.verbose)
        
        if not latest_art:
            results.append({
                "app_name": app["app_name"],
                "type": "Chart",
                "name": chart_name,
                "local_version": local_version,
                "target_version": "n/a",
                "status_text": "NOT FOUND",
                "status_color": COLOR_RED,
                "is_outdated": False,
                "meta": app
            })
        else:
            latest_version = latest_art.get("version", "")
            latest_revision = latest_art.get("revision", "")
            latest_full_version = f"{latest_version}-{latest_revision}" if latest_revision else latest_version
            
            # Resolve target version
            if version_format == "auto":
                if "-" in local_version:
                    target_version = latest_full_version
                else:
                    target_version = latest_version
            elif version_format == "full":
                target_version = latest_full_version
            else:
                target_version = latest_version
                
            is_up_to_date = (local_version == target_version)
            
            if is_up_to_date:
                status_text = "UP-TO-DATE"
                status_color = COLOR_GREEN
                is_outdated = False
            else:
                status_text = "OUTDATED"
                status_color = COLOR_YELLOW
                is_outdated = True
                
            results.append({
                "app_name": app["app_name"],
                "type": "Chart",
                "name": chart_name,
                "local_version": local_version,
                "target_version": target_version,
                "status_text": status_text,
                "status_color": status_color,
                "is_outdated": is_outdated,
                "meta": app
            })
            
            if is_outdated:
                outdated_charts.append({
                    "app": app,
                    "target_version": target_version
                })

        # 2. Check images inside chart (if helm is installed)
        if helm_installed:
            log_debug(f"Extracting images for {app['app_name']} using Helm...")
            images = get_helm_template_images(app["chart"], local_version, app.get("values"), verbose=args.debug or args.verbose)
            
            for img in images:
                # Resolve short name and version from image string
                # E.g. registry.suse.com/bci/bci-base:15.5 -> short_name="bci-base", tag="15.5"
                img_name_part = img.split("@")[0] # remove digest if present
                if ":" in img_name_part:
                    img_path, img_tag = img_name_part.rsplit(":", 1)
                else:
                    img_path = img_name_part
                    img_tag = "latest"
                    
                img_short_name = img_path.split("/")[-1]
                
                # Fetch newest image version from Appco API
                latest_img_art = fetch_latest_appco_image_version(api_url, img_short_name, verbose=args.debug or args.verbose)
                
                if not latest_img_art:
                    results.append({
                        "app_name": app["app_name"],
                        "type": "Image",
                        "name": f"  └─ {img_short_name}",
                        "local_version": img_tag,
                        "target_version": "n/a",
                        "status_text": "NOT FOUND",
                        "status_color": COLOR_RED,
                        "is_outdated": False,
                        "meta": {
                            "filepath": app["filepath"],
                            "app_name": app["app_name"],
                            "image_raw": img
                        }
                    })
                    continue
                    
                latest_img_version = latest_img_art.get("version", "")
                latest_img_revision = latest_img_art.get("revision", "")
                latest_img_full_version = f"{latest_img_version}-{latest_img_revision}" if latest_img_revision else latest_img_version
                
                # Resolve target version
                if version_format == "auto":
                    if "-" in img_tag:
                        target_img_version = latest_img_full_version
                    else:
                        target_img_version = latest_img_version
                elif version_format == "full":
                    target_img_version = latest_img_full_version
                else:
                    target_img_version = latest_img_version
                    
                is_img_up_to_date = (img_tag == target_img_version)
                
                if is_img_up_to_date:
                    status_text = "UP-TO-DATE"
                    status_color = COLOR_GREEN
                    is_img_outdated = False
                else:
                    status_text = "OUTDATED"
                    status_color = COLOR_YELLOW
                    is_img_outdated = True
                    
                results.append({
                    "app_name": app["app_name"],
                    "type": "Image",
                    "name": f"  └─ {img_short_name}",
                    "local_version": img_tag,
                    "target_version": target_img_version,
                    "status_text": status_text,
                    "status_color": status_color,
                    "is_outdated": is_img_outdated,
                    "meta": {
                        "filepath": app["filepath"],
                        "app_name": app["app_name"],
                        "image_raw": img
                    }
                })
                
                if is_img_outdated:
                    outdated_images.append({
                        "app_name": app["app_name"],
                        "filepath": app["filepath"],
                        "image_raw": img,
                        "image_short_name": img_short_name,
                        "local_version": img_tag,
                        "target_version": target_img_version,
                        "app_meta": app
                    })

    # Calculate dynamic column widths (with fallback minimums matching previous widths)
    col_app_width = max(20, max((len(r["app_name"]) for r in results), default=20))
    col_type_width = 10
    col_name_width = max(25, max((len(r["name"]) for r in results), default=25))
    col_local_v_width = max(18, max((len(r["local_version"]) for r in results), default=18))
    col_latest_v_width = max(18, max((len(r["target_version"]) for r in results), default=18))
    col_status_width = 15
    
    total_table_width = col_app_width + col_type_width + col_name_width + col_local_v_width + col_latest_v_width + col_status_width + 5 # 5 spaces in print
    
    print("-" * total_table_width)
    print(f"{COLOR_BOLD}{'Application':<{col_app_width}} {'Type':<{col_type_width}} {'Artifact Name':<{col_name_width}} {'Local Version':<{col_local_v_width}} {'Latest Appco':<{col_latest_v_width}} {'Status':<{col_status_width}}{COLOR_RESET}")
    print("-" * total_table_width)
    
    for r in results:
        status_colored = f"{r['status_color']}{r['status_text']:<{col_status_width}}{COLOR_RESET}"
        app_name_disp = "" if r["type"] == "Image" else r["app_name"]
        print(f"{app_name_disp:<{col_app_width}} {r['type']:<{col_type_width}} {r['name']:<{col_name_width}} {r['local_version']:<{col_local_v_width}} {r['target_version']:<{col_latest_v_width}} {status_colored}")
        
    print("-" * total_table_width)
    
    total_outdated = len(outdated_charts) + len(outdated_images)
    if total_outdated == 0:
        log_success("All Appco applications and container images are fully up-to-date!")
        sys.exit(0)
        
    if outdated_charts:
        log_info(f"{len(outdated_charts)} application chart(s) have new versions available.")
    if outdated_images:
        log_info(f"{len(outdated_images)} container image(s) have new versions available.")
    
    if args.apply:
        print("\nApplying version updates...")
        updated_count = 0
        for item in outdated_charts:
            app_meta = item["app"]
            target_v = item["target_version"]
            
            success = surgical_update_version(app_meta["filepath"], target_v)
            if success:
                log_success(f"Updated {COLOR_BOLD}{app_meta['app_name']}{COLOR_RESET} to version {COLOR_BOLD}{target_v}{COLOR_RESET} in {app_meta['filepath']}")
                updated_count += 1
            else:
                log_error(f"Failed to update version in {app_meta['filepath']}")
                
        log_success(f"Successfully updated {updated_count} application bundle(s).")
    else:
        if outdated_charts:
            print(f"\n{COLOR_YELLOW}Run with the '--apply' flag to automatically update the fleet.yaml files.{COLOR_RESET}")
            
    if args.apply_images:
        if not outdated_images:
            log_success("No outdated images to update.")
        else:
            print("\nApplying image updates...")
            updated_images_count = 0
            for item in outdated_images:
                filepath = item["filepath"]
                old_tag = item["local_version"]
                new_tag = item["target_version"]
                img_name = item["image_short_name"]
                image_raw = item.get("image_raw", "")
                
                success = surgical_update_image_tag(filepath, old_tag, new_tag)
                if success:
                    log_success(f"Updated image {COLOR_BOLD}{img_name}{COLOR_RESET} tag from {COLOR_BOLD}{old_tag}{COLOR_RESET} to {COLOR_BOLD}{new_tag}{COLOR_RESET} in {filepath}")
                    updated_images_count += 1
                else:
                    log_debug(f"Could not find tag '{old_tag}' to update in {filepath} (likely defined in Helm chart defaults). Attempting heuristic discovery...")
                    
                    # Resolve image repo (remove tag or digest)
                    img_repo = image_raw.split("@")[0].rsplit(":", 1)[0] if image_raw else ""
                    
                    # Pull chart default values
                    app_meta = item.get("app_meta", {})
                    chart_url = app_meta.get("chart")
                    chart_version = app_meta.get("local_version")
                    
                    if img_repo and chart_url and chart_version:
                        default_values = get_chart_default_values(chart_url, chart_version, verbose=args.debug or args.verbose)
                        val_path, tag_key = find_image_value_path(default_values, img_repo, old_tag)
                        
                        if val_path is not None and tag_key is not None:
                            if args.yes:
                                user_choice = 'y'
                            else:
                                confirm_prompt = f"\n{COLOR_YELLOW}[PROMPT]{COLOR_RESET} Tag '{old_tag}' for image {COLOR_BOLD}{img_name}{COLOR_RESET} is not defined in {filepath}.\n" \
                                                 f"Heuristically found path in chart defaults: helm.values.{'.'.join(val_path)}.{tag_key}\n" \
                                                 f"Do you want to inject override `{'.'.join(val_path)}.{tag_key}: \"{new_tag}\"` into {filepath}? [Y/n]: "
                                try:
                                    user_choice = input(confirm_prompt).strip().lower()
                                    if user_choice == '':
                                        user_choice = 'y' # Default to yes
                                except (KeyboardInterrupt, EOFError):
                                    print()
                                    continue
                                
                            if user_choice == 'y':
                                success = inject_image_tag_override(filepath, val_path, tag_key, new_tag)
                                if success:
                                    log_success(f"Successfully injected image override in {filepath}")
                                    updated_images_count += 1
                                else:
                                    log_error(f"Failed to inject image override in {filepath}")
                        else:
                            log_debug(f"Heuristic path discovery failed for image '{img_repo}' with tag '{old_tag}'")
                    else:
                        log_debug(f"Missing chart metadata or repository details for image '{img_name}'")
            
            if updated_images_count > 0:
                log_success(f"Successfully updated/injected {updated_images_count} image tag(s).")
            else:
                log_info("No image tags were updated or injected in fleet.yaml files.")
    else:
        if outdated_images:
            print(f"\n{COLOR_YELLOW}Run with the '--apply-images' flag to automatically update the image tags in the fleet.yaml files.{COLOR_RESET}")

if __name__ == "__main__":
    main()
