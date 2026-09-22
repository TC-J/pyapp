import tempfile
import unittest
from pathlib import Path

from pyapp.runtime import AppStorageManager, parse_spec


class ParseSpecTests(unittest.TestCase):
    def test_default_root_is_config(self) -> None:
        spec = parse_spec("pyapp.toml:a")
        self.assertEqual(spec.root, "config")
        self.assertEqual(spec.relative, Path("pyapp.toml"))
        self.assertEqual(spec.fmt, "toml")
        self.assertEqual(spec.keys, ("a",))

    def test_nested_path_and_keys(self) -> None:
        spec = parse_spec("config.db.settings.yaml:database.host")
        self.assertEqual(spec.root, "config")
        self.assertEqual(spec.relative, Path("db") / "settings.yaml")
        self.assertEqual(spec.fmt, "yaml")
        self.assertEqual(spec.keys, ("database", "host"))

    def test_data_prefix(self) -> None:
        spec = parse_spec("data.cache.state.json:count")
        self.assertEqual(spec.root, "data")
        self.assertEqual(spec.relative, Path("cache") / "state.json")
        self.assertEqual(spec.fmt, "json")
        self.assertEqual(spec.keys, ("count",))

    def test_filename_matching_root_name(self) -> None:
        spec = parse_spec("data.json:x")
        self.assertEqual(spec.root, "config")
        self.assertEqual(spec.relative, Path("data.json"))

    def test_directory_and_root(self) -> None:
        logs = parse_spec("data.cache.logs")
        self.assertEqual(logs.root, "data")
        self.assertEqual(logs.relative, Path("cache") / "logs")
        self.assertIsNone(logs.fmt)

        root = parse_spec("config")
        self.assertEqual(root.root, "config")
        self.assertEqual(root.relative, Path())

    def test_rejects_traversal(self) -> None:
        with self.assertRaises(ValueError):
            parse_spec("config...secret.toml:x")
        with self.assertRaises(ValueError):
            parse_spec("data.foo/bar.json:x")


class StorageManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.store = AppStorageManager(
            "test-app",
            "",
            base_data_dir=root / "data",
            base_config_dir=root / "config",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_creates_platform_override_dirs(self) -> None:
        self.assertTrue(self.store.data_dir.is_dir())
        self.assertTrue(self.store.config_dir.is_dir())

    def test_toml_set_get(self) -> None:
        self.store.set("pyapp.toml:a", "hi")
        self.assertEqual(self.store.get("pyapp.toml:a"), "hi")
        self.assertEqual(self.store.get("config.pyapp.toml:a"), "hi")
        self.assertTrue((self.store.config_dir / "pyapp.toml").is_file())

    def test_nested_json_and_yaml(self) -> None:
        self.store.set("data.cache.state.json:users.0.name", "ada")
        self.assertEqual(self.store.get("data.cache.state.json:users.0.name"), "ada")
        self.assertEqual(
            self.store.get("data.cache.state.json"),
            {"users": [{"name": "ada"}]},
        )
        self.store.set("foo.bar.yaml:db.host", "localhost")
        self.store.set("foo.bar.yaml:db.port", 5432)
        self.assertEqual(self.store.get("foo.bar.yaml:db.host"), "localhost")
        self.assertEqual(self.store.get("config.foo.bar.yaml:db.port"), 5432)
        self.assertEqual(
            self.store.get("foo.bar.yaml"),
            {"db": {"host": "localhost", "port": 5432}},
        )

    def test_json_whole_file_and_default(self) -> None:
        self.store.set("prefs.json", {"theme": "dark"})
        self.assertEqual(self.store.get("prefs.json:theme"), "dark")
        self.assertIsNone(self.store.get("prefs.json:missing"))
        self.assertEqual(self.store.get("prefs.json:missing", default=1), 1)
        self.assertEqual(self.store.get("absent.toml:x", default="fallback"), "fallback")

    def test_ini_section_option(self) -> None:
        self.store.set("app.ini:database.host", "localhost")
        self.store.set("app.ini:database.port", 5432)
        self.assertEqual(self.store.get("app.ini:database.host"), "localhost")
        self.assertEqual(self.store.get("app.ini:database.port"), 5432)
        self.assertEqual(self.store.get("app.ini:database")["host"], "localhost")

    def test_data_vs_config_isolation(self) -> None:
        self.store.set("config.state.json:n", 1)
        self.store.set("data.state.json:n", 2)
        self.assertEqual(self.store.get("config.state.json:n"), 1)
        self.assertEqual(self.store.get("data.state.json:n"), 2)

    def test_resolve_ensures_directories(self) -> None:
        path = self.store.resolve("data.cache.images", ensure=True)
        self.assertTrue(path.is_dir())
        self.assertEqual(path, (self.store.data_dir / "cache" / "images").resolve())

    def test_directory_spec_rejected_for_get_set(self) -> None:
        with self.assertRaises(ValueError):
            self.store.get("data.cache")
        with self.assertRaises(ValueError):
            self.store.set("data.cache", {"x": 1})

    def test_fluent_set(self) -> None:
        value = self.store.set("a.toml:one", 1).set("a.toml:two", 2).get("a.toml")
        self.assertEqual(value, {"one": 1, "two": 2})


if __name__ == "__main__":
    unittest.main()
