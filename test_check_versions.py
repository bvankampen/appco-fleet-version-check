#!/usr/bin/env python3
"""
Unit tests for Appco Version Checker (check_versions.py).
"""

import os
import tempfile
import shutil
import unittest
from unittest.mock import patch, MagicMock
from check_versions import parse_version_key, surgical_update_version, scan_fleet_directory, surgical_update_image_tag, get_helm_template_images

class TestAppcoVersionChecker(unittest.TestCase):

    def test_parse_version_key(self):
        # Standard semver
        self.assertEqual(parse_version_key("1.21.1"), ((1, 21, 1), (0, 0)))
        self.assertEqual(parse_version_key("v0.34.0"), ((0, 34, 0), (0, 0)))
        
        # Semver with revision
        self.assertEqual(parse_version_key("1.21.1-2.1"), ((1, 21, 1), (2, 1)))
        self.assertEqual(parse_version_key("v0.34.0-18.1"), ((0, 34, 0), (18, 1)))
        self.assertEqual(parse_version_key("1.2.0-5.6"), ((1, 2, 0), (5, 6)))
        
        # Build metadata and other formats
        self.assertEqual(parse_version_key("109.0.3+up80.9.1-rancher.14"), ((109, 0, 3), (14, 0)))
        self.assertEqual(parse_version_key(""), ((0, 0, 0), (0, 0)))
        self.assertEqual(parse_version_key(None), ((0, 0, 0), (0, 0)))

        # Sorting check
        v1 = parse_version_key("1.21.1-1.1")
        v2 = parse_version_key("1.21.1-2.1")
        v3 = parse_version_key("1.21.2")
        self.assertTrue(v1 < v2)
        self.assertTrue(v2 < v3)

    def test_surgical_update_version(self):
        # Create a temporary file representing a fleet.yaml
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.yaml', delete=False) as f:
            f.write("""defaultNamespace: test-app

helm:
  chart: oci://registry.lab.suse/dp.apps.rancher.io/charts/vault
  releaseName: test-app
  version: 1.2.0 # Keep this comment
  takeOwnership: true
""")
            temp_path = f.name

        try:
            # Perform surgical update
            success = surgical_update_version(temp_path, "1.2.1-3.2")
            self.assertTrue(success)

            # Read file back and check content
            with open(temp_path, 'r') as f:
                content = f.read()

            self.assertIn("version: 1.2.1-3.2 # Keep this comment", content)
            self.assertIn("takeOwnership: true", content)
            self.assertIn("defaultNamespace: test-app", content)
        finally:
            os.remove(temp_path)

    def test_scan_fleet_directory(self):
        # Create a temporary directory structure mimicking the fleet bundles
        temp_dir = tempfile.mkdtemp()
        try:
            # 1. Appco application
            app1_dir = os.path.join(temp_dir, "vault", "app")
            os.makedirs(app1_dir)
            with open(os.path.join(app1_dir, "fleet.yaml"), "w") as f:
                f.write("""helm:
  chart: oci://registry.lab.suse/dp.apps.rancher.io/charts/vault
  version: "0.34.0"
""")

            # 2. Non-Appco application (different chart registry)
            app2_dir = os.path.join(temp_dir, "other-app", "app")
            os.makedirs(app2_dir)
            with open(os.path.join(app2_dir, "fleet.yaml"), "w") as f:
                f.write("""helm:
  chart: oci://registry.lab.suse/charts/other-app
  version: "1.0.0"
""")

            # Perform scan
            appco_domains = ["dp.apps.rancher.io"]
            apps = scan_fleet_directory(temp_dir, appco_domains)

            # Assert that only the Appco app was scanned and found
            self.assertEqual(len(apps), 1)
            self.assertEqual(apps[0]["app_name"], "vault")
            self.assertEqual(apps[0]["chart_name"], "vault")
            self.assertEqual(apps[0]["local_version"], "0.34.0")

        finally:
            shutil.rmtree(temp_dir)

    def test_surgical_update_image_tag(self):
        # Create a temporary file representing a fleet.yaml with helm values
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.yaml', delete=False) as f:
            f.write("""defaultNamespace: test-app

helm:
  chart: oci://registry.lab.suse/dp.apps.rancher.io/charts/vault
  version: 1.2.0
  values:
    image:
      repository: registry.suse.com/rancher/vault
      tag: "1.14.0" # Keep this comment
    global:
      someOtherTag: 1.14.0
""")
            temp_path = f.name

        try:
            # Perform surgical update of image tag
            success = surgical_update_image_tag(temp_path, "1.14.0", "1.15.2")
            self.assertTrue(success)

            # Read file back and check content
            with open(temp_path, 'r') as f:
                content = f.read()

            self.assertIn('tag: "1.15.2" # Keep this comment', content)
            self.assertIn('someOtherTag: 1.15.2', content)
            self.assertIn("version: 1.2.0", content)
            self.assertIn("defaultNamespace: test-app", content)
        finally:
            os.remove(temp_path)

    @patch("subprocess.run")
    def test_get_helm_template_images(self, mock_run):
        # Mock successful responses for 'helm pull' and 'helm template'
        mock_pull_res = MagicMock()
        mock_pull_res.returncode = 0
        
        mock_template_res = MagicMock()
        mock_template_res.returncode = 0
        mock_template_res.stdout = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vault
spec:
  template:
    spec:
      containers:
        - name: vault
          image: registry.suse.com/rancher/vault:1.14.0
        - name: helper
          image: "registry.suse.com/rancher/helper-image:v1.2.3"
      initContainers:
        - name: init-vault
          image: registry.suse.com/rancher/init-vault:v0.1.0@sha256:12345abcdef
"""
        
        # subprocess.run is called twice: once for pull, once for template
        mock_run.side_effect = [mock_pull_res, mock_template_res]
        
        # We need to mock os.path.exists and os.listdir to simulate find chart folder in temp_dir
        with patch("os.path.exists", return_value=True), \
             patch("os.listdir", return_value=["vault"]):
            
            images = get_helm_template_images(
                "oci://registry.lab.suse/dp.apps.rancher.io/charts/vault",
                "1.2.0",
                {"image": {"tag": "1.14.0"}}
            )
            
            # Assert exact expected images were extracted (and sorted/cleaned of digests)
            expected_images = [
                "registry.suse.com/rancher/helper-image:v1.2.3",
                "registry.suse.com/rancher/init-vault:v0.1.0@sha256:12345abcdef",
                "registry.suse.com/rancher/vault:1.14.0"
            ]
            self.assertEqual(images, expected_images)

if __name__ == '__main__':
    unittest.main()
