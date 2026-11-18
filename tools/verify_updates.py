"""验证匿名更新检查、版本判断和安装包完整性。"""

import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inkwell import updates
from inkwell.api import Api


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.content = b"installation package"
        self.asset = {"name": "Inkwell-Setup.zip", "state": "uploaded", "size": len(self.content),
                      "digest": "sha256:" + hashlib.sha256(self.content).hexdigest()}
        self.release = {"tag_name": "v1.10.0", "assets": [self.asset]}
        self.api = Api()

    def check(self):
        with patch.object(updates, "__version__", "1.9.9"), patch.object(updates.sys, "platform", "win32"), \
                patch.object(updates, "urlopen", return_value=io.BytesIO(json.dumps(self.release).encode())) as get:
            result = self.api.check_update()
        request = get.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(dict(request.header_items()), {"User-agent": "Inkwell", "Accept-encoding": "identity"})
        self.assertNotIn("github", json.dumps(result))
        return result

    def test_versions(self):
        self.assertTrue(self.check()["downloadable"])
        for tag in ("v1.9.9", "v1.1.4"):
            self.release["tag_name"] = tag
            self.assertFalse(self.check()["newer"])
            self.assertFalse(self.api.download_update()["ok"])

    def test_unavailable_package(self):
        self.asset["name"] = "other.zip"
        self.assertFalse(self.check()["downloadable"])

    def test_bad_metadata(self):
        self.release["prerelease"] = True
        self.assertFalse(self.check()["ok"])
        self.release["prerelease"] = False
        self.release["tag_name"] = "dev"
        self.assertFalse(self.check()["ok"])

    def test_service_errors(self):
        for error in (TimeoutError(), HTTPError("https://example.invalid", 429, "", {}, None)):
            with patch.object(updates, "_request", side_effect=error):
                self.assertFalse(self.api.check_update()["ok"])

    def test_download_dialog(self):
        self.check()
        self.api._window = Mock()
        dialog = self.api._window.create_file_dialog
        dialog.return_value = None
        with patch.object(updates, "_request") as download:
            self.assertTrue(self.api.download_update()["cancelled"])
            download.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / self.asset["name"]
            dialog.return_value = (str(path),)
            with patch.object(updates, "_request", return_value=io.BytesIO(self.content)):
                self.assertTrue(self.api.download_update()["ok"])
            self.assertEqual(path.read_bytes(), self.content)

    def test_download(self):
        self.check()
        package = self.api._update_package
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / package.name
            path.write_bytes(b"previous")
            for invalid in (b"", self.content[::-1], self.content + b"x"):
                with patch.object(updates, "_request", return_value=io.BytesIO(invalid)), self.assertRaises(ValueError):
                    updates.download_package(package, path)
                self.assertEqual(list(path.parent.iterdir()), [path])
                self.assertEqual(path.read_bytes(), b"previous")
            with patch.object(updates, "_request", return_value=io.BytesIO(self.content)):
                updates.download_package(package, path)
            self.assertEqual(path.read_bytes(), self.content)


if __name__ == "__main__":
    unittest.main()
