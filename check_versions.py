#!/usr/bin/env python3
"""
Rancher Prime Application Collection (Appco) Version Checker
Checks local Fleet application bundles against the latest versions in the Appco registry.
"""

import os
import re
import sys
import yaml
import argparse
import requests

# ANSI Color codes for clean terminal output
COLOR_RESET = "\033[0m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_RED = "\033[91m"
COLOR_BLUE = "\033[94m"
COLOR_BOLD = "\033[1m"

def log_info(msg):
    print(f"{COLOR_BLUE}[INFO]{COLOR_RESET} {msg}")

def log_success(msg):
    print(f"{COLOR_GREEN}[SUCCESS]{COLOR_RESET} {msg}")

def log_warn(msg):
    print(f"{COLOR_YELLOW}[WARN]{COLOR_RESET} {msg}")

def log_error(msg):
    print(f"{COLOR_RED}[ERROR]{COLOR_RESET} {msg}", file=sys.stderr)

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

def fetch_latest_appco_version(api_url, chart_name, verbose=False):
    """
    Queries Appco API for available HELM_CHART artifacts matching chart_name,
    and returns the latest artifact item based on custom semver sorting.
    """
    url = f"{api_url.rstrip('/')}/v1/artifacts"
    params = {
        "packaging_formats": "HELM_CHART",
        "name": chart_name,
        "page_size": 100
    }
    
    if verbose:
        log_info(f"API Request: GET {url} with params {params}")
        
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        artifacts = data.get("items", [])
        
        # Filter for exact chart name matching (artifact name must start with 'chart_name:')
        matching_artifacts = []
        for art in artifacts:
            name = art.get("name", "")
            if name.startswith(f"{chart_name}:"):
                matching_artifacts.append(art)
                
        if not matching_artifacts:
            if verbose:
                log_warn(f"No matching artifacts starting with '{chart_name}:' in API response.")
            return None
            
        # Sort artifacts descending by version key
        matching_artifacts.sort(
            key=lambda x: parse_version_key(x.get("name", "").split(":")[-1]),
            reverse=True
        )
        return matching_artifacts[0]
        
    except Exception as e:
        log_error(f"Failed to fetch artifacts for {chart_name} from Appco API: {e}")
        return None

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
            
            # Determine application folder name relative to fleet directory
            rel_path = os.path.relpath(root, fleet_dir)
            app_folder_name = rel_path.split(os.sep)[0] if rel_path != "." else os.path.basename(root)
            
            # If target_app is specified, skip unrelated apps
            if target_app and app_folder_name != target_app:
                continue
                
            try:
                with open(filepath, 'r') as f:
                    data = yaml.safe_load(f) or {}
            except Exception as e:
                log_warn(f"Failed to parse {filepath}: {e}. Skipping.")
                continue
                
            helm = data.get("helm", {})
            chart = helm.get("chart")
            version = str(helm.get("version", "")) if helm.get("version") is not None else ""
            
            if not chart or not version:
                if verbose:
                    log_info(f"Skipping {filepath}: No chart or version specified under helm:")
                continue
                
            # Check if chart is from Appco domains
            is_appco = False
            for domain in appco_domains:
                if domain in chart:
                    is_appco = True
                    break
                    
            if not is_appco:
                if verbose:
                    log_info(f"Skipping {filepath} ({app_folder_name}): Chart '{chart}' does not match Appco domain pattern.")
                continue
                
            # Extract the raw chart name from the registry path
            # E.g. oci://registry.lab.suse/dp.apps.rancher.io/charts/vault -> vault
            chart_name = chart.split("/")[-1].split(":")[0].split("@")[0]
            
            apps.append({
                "app_name": app_folder_name,
                "filepath": filepath,
                "chart": chart,
                "chart_name": chart_name,
                "local_version": version
            })
            
    return apps

def main():
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
        "--app",
        help="Check or apply changes only to a single specified application folder name."
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output logging."
    )
    
    args = parser.parse_args()
    
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
        
    log_info(f"Found {len(apps)} Appco application bundles. Verifying versions...")
    
    results = []
    outdated_apps = []
    
    for app in apps:
        chart_name = app["chart_name"]
        local_version = app["local_version"]
        
        # Fetch newest version from Appco API
        latest_art = fetch_latest_appco_version(api_url, chart_name, verbose=args.verbose)
        
        if not latest_art:
            results.append({
                "app_name": app["app_name"],
                "chart_name": chart_name,
                "local_version": local_version,
                "target_version": "n/a",
                "status_text": "NOT FOUND",
                "status_color": COLOR_RED,
                "is_outdated": False,
                "app_meta": app
            })
            continue
            
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
            "chart_name": chart_name,
            "local_version": local_version,
            "target_version": target_version,
            "status_text": status_text,
            "status_color": status_color,
            "is_outdated": is_outdated,
            "app_meta": app
        })
        
        if is_outdated:
            outdated_apps.append({
                "app": app,
                "target_version": target_version
            })

    # Calculate dynamic column widths (with fallback minimums matching previous widths)
    col_app_width = max(25, max((len(r["app_name"]) for r in results), default=25))
    col_chart_width = max(28, max((len(r["chart_name"]) for r in results), default=28))
    col_local_v_width = max(18, max((len(r["local_version"]) for r in results), default=18))
    col_latest_v_width = max(18, max((len(r["target_version"]) for r in results), default=18))
    col_status_width = 15
    
    total_table_width = col_app_width + col_chart_width + col_local_v_width + col_latest_v_width + col_status_width + 4 # 4 single spaces in print
    
    print("-" * total_table_width)
    print(f"{COLOR_BOLD}{'Application':<{col_app_width}} {'Chart Name':<{col_chart_width}} {'Local Version':<{col_local_v_width}} {'Latest Appco':<{col_latest_v_width}} {'Status':<{col_status_width}}{COLOR_RESET}")
    print("-" * total_table_width)
    
    for r in results:
        status_colored = f"{r['status_color']}{r['status_text']:<{col_status_width}}{COLOR_RESET}"
        print(f"{r['app_name']:<{col_app_width}} {r['chart_name']:<{col_chart_width}} {r['local_version']:<{col_local_v_width}} {r['target_version']:<{col_latest_v_width}} {status_colored}")
        
    print("-" * total_table_width)
    
    if not outdated_apps:
        log_success("All Appco applications are fully up-to-date!")
        sys.exit(0)
        
    log_info(f"{len(outdated_apps)} application(s) have new versions available.")
    
    if args.apply:
        print("\nApplying version updates...")
        updated_count = 0
        for item in outdated_apps:
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
        print(f"\n{COLOR_YELLOW}Run with the '--apply' flag to automatically update the fleet.yaml files.{COLOR_RESET}")

if __name__ == "__main__":
    main()
