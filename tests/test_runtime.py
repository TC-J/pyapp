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

    def test_omitted_extension(self) -> None:
        spec = parse_spec("pyapp:a")
        self.assertEqual(spec.root, "config")
        self.assertEqual(spec.relative, Path("pyapp"))
        self.assertIsNone(spec.fmt)
        self.assertEqual(spec.keys, ("a",))

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

    def test_directory_roots_rejected_for_get_set(self) -> None:
        with self.assertRaises(ValueError):
            self.store.get("data")
        with self.assertRaises(ValueError):
            self.store.set("config", {"x": 1})

    def test_optional_extension(self) -> None:
        self.store["pyapp:a"] = "hi"
        self.assertEqual(self.store["pyapp:a"], "hi")
        self.assertEqual(self.store.get("pyapp.yaml:a"), "hi")
        self.assertTrue((self.store.config_dir / "pyapp.yaml").is_file())

        self.store.set("data.cache.state.json:n", 1)
        self.assertEqual(self.store.get("data.cache.state:n"), 1)
        self.assertEqual(self.store.load("data.cache.state"), {"n": 1})
        self.assertTrue(self.store.exists("data.cache.state"))

        json_store = AppStorageManager(
            "test-app",
            "",
            base_data_dir=self.store.data_dir,
            base_config_dir=self.store.config_dir / "json-default",
            default_format="json",
        )
        json_store["prefs:theme"] = "dark"
        self.assertTrue((json_store.config_dir / "prefs.json").is_file())
        self.assertEqual(json_store.load("prefs"), {"theme": "dark"})

    def test_ambiguous_extension_raises(self) -> None:
        self.store.set("dual.toml", {"a": 1})
        self.store.set("dual.json", {"a": 2})
        with self.assertRaises(ValueError):
            self.store.get("dual")
        self.assertEqual(self.store.get("dual.toml:a"), 1)
        self.assertEqual(self.store.get("dual.json:a"), 2)

    def test_create_file_without_extension(self) -> None:
        path = self.store.create("notes", {"pinned": True})
        self.assertEqual(path.name, "notes.yaml")
        self.assertEqual(self.store.load("notes"), {"pinned": True})
        self.assertEqual(self.store.load("notes.yaml"), {"pinned": True})

    def test_fluent_set(self) -> None:
        value = self.store.set("a.toml:one", 1).set("a.toml:two", 2).get("a.toml")
        self.assertEqual(value, {"one": 1, "two": 2})

    def test_bracket_get_set(self) -> None:
        self.store["pyapp.toml:a"] = "hi"
        self.assertEqual(self.store["pyapp.toml:a"], "hi")
        self.assertEqual(self.store["pyapp.toml"], {"a": "hi"})
        self.assertIn("pyapp.toml:a", self.store)
        self.assertNotIn("pyapp.toml:missing", self.store)
        with self.assertRaises(KeyError):
            _ = self.store["pyapp.toml:missing"]

    def test_patch_creates_and_merges(self) -> None:
        self.store.patch(
            "settings.toml",
            {"database": {"host": "localhost", "port": 5432}},
        )
        self.store.patch(
            "settings.toml",
            {"database": {"host": "127.0.0.1"}, "debug": True},
        )
        self.assertEqual(
            self.store.load("settings.toml"),
            {"database": {"host": "127.0.0.1", "port": 5432}, "debug": True},
        )

    def test_patch_nested_key_and_data_root(self) -> None:
        self.store.patch("data.cache.state.json:users", {"ada": {"id": 1}})
        self.store.patch("data.cache.state.json:users", {"ada": {"role": "admin"}})
        self.assertEqual(
            self.store["data.cache.state.json:users.ada"],
            {"id": 1, "role": "admin"},
        )

    def test_create_and_load_files(self) -> None:
        path = self.store.create("config.prefs.json")
        self.assertTrue(path.is_file())
        self.assertEqual(self.store.load("prefs.json"), {})
        existing = self.store.create("prefs.json", {"theme": "dark"})
        self.assertEqual(existing, path)
        self.assertEqual(self.store.load("prefs.json"), {})
        self.store.create("data.cache.notes.yaml", {"pinned": True})
        self.assertEqual(self.store.load("data.cache.notes.yaml"), {"pinned": True})
        self.assertEqual(self.store.load("data.cache.notes.yaml:pinned"), True)
        with self.assertRaises(FileExistsError):
            self.store.create("prefs.json", exist_ok=False)
        self.assertEqual(self.store.load("missing.toml"), {})
        self.assertTrue((self.store.config_dir / "missing.toml").is_file())

    def test_create_directory(self) -> None:
        path = self.store.create("data.cache.images")
        self.assertTrue(path.is_dir())
        self.assertTrue(self.store.exists("data.cache.images"))
        with self.assertRaises(ValueError):
            self.store.create("prefs.json:theme")

    def test_get_creates_missing_file(self) -> None:
        self.assertFalse(self.store.exists("settings"))
        self.assertIsNone(self.store.get("settings:cluster_name"))
        self.assertTrue((self.store.config_dir / "settings.yaml").is_file())
        self.assertEqual(self.store.get("settings"), {})
        self.assertNotIn("settings:cluster_name", self.store)
        self.assertIsNone(self.store.get("explicit.toml:missing"))
        self.assertTrue((self.store.config_dir / "explicit.toml").is_file())

    def test_ensure_file_and_dir(self) -> None:
        path = self.store.ensure_file("data.cache.state")
        self.assertEqual(path, (self.store.data_dir / "cache" / "state.yaml").resolve())
        self.assertTrue(path.is_file())
        self.assertEqual(self.store.load("data.cache.state"), {})

        keyed = self.store.ensure_file("config.prefs:theme")
        self.assertEqual(keyed.name, "prefs.yaml")
        self.assertTrue(keyed.is_file())

        existing = self.store.ensure_file("data.cache.state.json")
        self.assertEqual(existing.name, "state.json")
        self.assertTrue(existing.is_file())

        images = self.store.ensure_dir("data.cache.images")
        self.assertTrue(images.is_dir())
        with self.assertRaises(ValueError):
            self.store.ensure_dir("settings.yaml")
        with self.assertRaises(ValueError):
            self.store.ensure_dir("settings:name")


if __name__ == "__main__":
    unittest.main()
