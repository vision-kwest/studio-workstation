"""Offline contracts for standalone DCV permissions-file readability."""
import ast
import grp
import os
from pathlib import Path
import pwd
import stat
import tempfile
import types
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "runtime/overrides/ideabootstrap/dcv/dcv.py"
TREE = ast.parse(SOURCE.read_text())
FN = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_secure_standalone_permissions_file")
NS = {"os": os, "grp": grp, "pwd": pwd, "stat": stat}
exec(compile(ast.Module(body=[FN], type_ignores=[]), str(SOURCE), "exec"), NS)
secure = NS["_secure_standalone_permissions_file"]

class DCVPermissionsTests(unittest.TestCase):
    def test_original_console_lifecycle_retained(self):
        source = SOURCE.read_text()
        self.assertIn("dcv.dcv_host.apply_automatic_console_session_config(", source)
        self.assertIn("if is_dcvserver_ready():", source)
        self.assertIn("_secure_standalone_permissions_file(permissions_file_path)", source)
        self.assertNotIn("os.chmod(permissions_file_path, 0o600)", source)

    def test_restrictive_group_read_and_traversal(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "idea.perm"
            path.write_text("[permissions]\n%owner% allow builtin\n")
            os.chmod(root, 0o755)
            real_stat = os.stat
            def stat_file(p, *args, **kwargs):
                st = real_stat(p, *args, **kwargs)
                if str(p) == str(path):
                    fields = list(st)
                    fields[4] = 0
                    fields[5] = os.getgid()
                    return os.stat_result(fields)
                return st
            with patch.object(grp, "getgrnam", return_value=types.SimpleNamespace(gr_gid=os.getgid())), \
                 patch.object(pwd, "getpwnam", return_value=types.SimpleNamespace(pw_uid=42424)), \
                 patch.object(os, "chown") as chown, \
                 patch.object(os, "stat", side_effect=stat_file):
                secure(str(path))
                chown.assert_called_once_with(str(path), 0, os.getgid())
            self.assertEqual(stat.S_IMODE(real_stat(path).st_mode), 0o640)

    def test_untraversable_parent_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "idea.perm"
            path.write_text("test")
            os.chmod(root, 0o700)
            real_stat = os.stat
            def stat_file(p, *args, **kwargs):
                st = real_stat(p, *args, **kwargs)
                if str(p) == str(path):
                    fields = list(st)
                    fields[4] = 0
                    fields[5] = os.getgid()
                    return os.stat_result(fields)
                return st
            with patch.object(grp, "getgrnam", return_value=types.SimpleNamespace(gr_gid=os.getgid())), \
                 patch.object(pwd, "getpwnam", return_value=types.SimpleNamespace(pw_uid=42424)), \
                 patch.object(os, "chown"), patch.object(os, "stat", side_effect=stat_file):
                with self.assertRaisesRegex(RuntimeError, "cannot traverse"):
                    secure(str(path))

if __name__ == "__main__":
    unittest.main()
