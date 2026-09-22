"""Application data and configuration storage on platform-standard directories."""

from __future__ import annotations

import configparser
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self

import tomli_w
import yaml
from platformdirs import PlatformDirs

RootName = Literal["data", "config"]
FileFormat = Literal["toml", "yaml", "json", "ini"]

_FORMATS: dict[str, FileFormat] = {
    "toml": "toml",
    "yaml": "yaml",
    "yml": "yaml",
    "json": "json",
    "ini": "ini",
}
_ROOTS = frozenset({"data", "config"})
_MISSING = object()


@dataclass(frozen=True, slots=True)
class StorageSpec:
    """A parsed storage locator: root, relative path, optional format, and key path."""

    root: RootName
    relative: Path
    fmt: FileFormat | None
    keys: tuple[str, ...]

    @property
    def is_file(self) -> bool:
        return self.fmt is not None


def parse_spec(spec: str) -> StorageSpec:
    """Parse a dotted storage spec into a path relative to a platform dir.

    Specs use dots as path separators. An optional ``data.`` or ``config.`` prefix
    selects the root; files without a prefix go under the config directory.

    Examples::

        pyapp.toml:a              -> {config}/pyapp.toml  key a
        config.settings.yaml:db.host
        data.cache.state.json:count
        data.logs                 -> {data}/logs  (directory)
    """
    if not spec or spec.isspace():
        raise ValueError("storage spec must be a non-empty string")

    file_part, keys = _split_keys(spec)
    parts = file_part.split(".")
    if any(part == "" for part in parts):
        raise ValueError(f"invalid storage spec {spec!r}: empty path segment")
    _reject_unsafe_parts(parts, spec)

    root, parts = _consume_root(parts)
    if not parts:
        return StorageSpec(root=root, relative=Path(), fmt=None, keys=keys)

    suffix = parts[-1]
    fmt = _FORMATS.get(suffix)
    if fmt is not None:
        if len(parts) < 2:
            raise ValueError(
                f"invalid storage spec {spec!r}: file name missing before .{suffix}"
            )
        relative = Path(*parts[:-2], f"{parts[-2]}.{suffix}") if len(parts) > 2 else Path(
            f"{parts[0]}.{suffix}"
        )
        return StorageSpec(root=root, relative=relative, fmt=fmt, keys=keys)

    return StorageSpec(root=root, relative=Path(*parts), fmt=None, keys=keys)


class AppStorageManager:
    """Manage per-app data and config directories, with typed file key access.

    Paths are expressed with dot syntax relative to the platform data/config
    directories from :mod:`platformdirs`. Override those roots with
    ``base_data_dir`` / ``base_config_dir`` (useful in tests).

    Get and set nested keys in TOML, YAML, JSON, and INI files::

        store = AppStorageManager("my-app", "Acme")
        store.set("settings.toml:database.host", "localhost")
        store.get("data.cache.state.json:count", default=0)
    """

    def __init__(
        self,
        appname: str,
        appauthor: str | None = None,
        version: str | None = None,
        *,
        base_data_dir: str | Path | None = None,
        base_config_dir: str | Path | None = None,
        ensure_exists: bool = True,
        roaming: bool = False,
    ) -> None:
        author: str | None | Literal[False]
        if appauthor == "":
            author = None
        else:
            author = appauthor

        dirs = PlatformDirs(
            appname,
            author,
            version=version,
            roaming=roaming,
            ensure_exists=False,
        )
        self._data_dir = (
            Path(base_data_dir) if base_data_dir is not None else dirs.user_data_path
        )
        self._config_dir = (
            Path(base_config_dir)
            if base_config_dir is not None
            else dirs.user_config_path
        )
        if ensure_exists:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._config_dir.mkdir(parents=True, exist_ok=True)

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def config_dir(self) -> Path:
        return self._config_dir

    def resolve(self, spec: str | StorageSpec, *, ensure: bool = False) -> Path:
        """Resolve a dotted spec to an absolute path under data or config."""
        parsed = spec if isinstance(spec, StorageSpec) else parse_spec(spec)
        root = self._root(parsed.root)
        path = (root / parsed.relative).resolve()
        if ensure:
            if parsed.is_file:
                path.parent.mkdir(parents=True, exist_ok=True)
            else:
                path.mkdir(parents=True, exist_ok=True)
        return path

    def get(self, spec: str, default: Any = None) -> Any:
        """Read a file or nested key. Missing files/keys return ``default``."""
        parsed = parse_spec(spec)
        fmt = self._require_file(parsed, spec)
        path = self.resolve(parsed)
        if not path.is_file():
            return default
        document = _read_document(path, fmt)
        if not parsed.keys:
            return document if fmt != "ini" else _ini_as_dict(document)
        value = _lookup(document, parsed.keys, default=_MISSING)
        return default if value is _MISSING else value

    def set(self, spec: str, value: Any) -> Self:
        """Write a file or nested key, creating parents and the file as needed."""
        parsed = parse_spec(spec)
        fmt = self._require_file(parsed, spec)
        path = self.resolve(parsed, ensure=True)
        if parsed.keys:
            document = (
                _read_document(path, fmt)
                if path.is_file() and path.stat().st_size > 0
                else _empty_document(fmt)
            )
            document = _assign(document, parsed.keys, value, fmt)
        else:
            document = _prepare_whole_document(fmt, value)
        _write_document(path, fmt, document)
        return self

    def _root(self, name: RootName) -> Path:
        return self._data_dir if name == "data" else self._config_dir

    @staticmethod
    def _require_file(parsed: StorageSpec, spec: str) -> FileFormat:
        if parsed.fmt is None:
            raise ValueError(
                f"storage spec {spec!r} is a directory; get/set require "
                "a .toml, .yaml, .yml, .json, or .ini file"
            )
        return parsed.fmt


def _split_keys(spec: str) -> tuple[str, tuple[str, ...]]:
    if ":" not in spec:
        return spec, ()
    file_part, key_part = spec.split(":", 1)
    if not file_part:
        raise ValueError(f"invalid storage spec {spec!r}: missing path before ':'")
    if key_part == "":
        return file_part, ()
    keys = tuple(key_part.split("."))
    if any(key == "" for key in keys):
        raise ValueError(f"invalid storage spec {spec!r}: empty key segment")
    return file_part, keys


def _reject_unsafe_parts(parts: list[str], spec: str) -> None:
    for part in parts:
        if part in {".", ".."} or "/" in part or "\\" in part:
            raise ValueError(f"invalid storage spec {spec!r}: illegal path segment {part!r}")


def _consume_root(parts: list[str]) -> tuple[RootName, list[str]]:
    """Take a leading data/config token when it is a root, not a file stem."""
    if parts[0] not in _ROOTS:
        return "config", parts
    if len(parts) == 1:
        return parts[0], []  # type: ignore[return-value]
    rest = parts[1:]
    last = rest[-1]
    if last in _FORMATS and len(rest) == 1:
        # "data.json" / "config.toml" are filenames under the default config root.
        return "config", parts
    return parts[0], rest  # type: ignore[return-value]


def _new_ini_parser() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    return parser


def _empty_document(fmt: FileFormat) -> Any:
    if fmt == "ini":
        return _new_ini_parser()
    return {}


def _prepare_whole_document(fmt: FileFormat, value: Any) -> Any:
    if fmt == "ini":
        return _ini_from_mapping(value)
    if fmt == "toml" and not isinstance(value, dict):
        raise TypeError(
            f"whole-file set for toml requires a dict, got {type(value).__name__}"
        )
    return value


def _read_document(path: Path, fmt: FileFormat) -> Any:
    if path.stat().st_size == 0:
        return _empty_document(fmt)
    if fmt == "json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return {} if data is None else data
    if fmt == "yaml":
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return {} if data is None else data
    if fmt == "toml":
        with path.open("rb") as handle:
            return tomllib.load(handle)
    parser = _new_ini_parser()
    parser.read(path, encoding="utf-8")
    return parser


def _write_document(path: Path, fmt: FileFormat, document: Any) -> None:
    if fmt == "json":
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        return
    if fmt == "yaml":
        path.write_text(
            yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return
    if fmt == "toml":
        if not isinstance(document, dict):
            raise TypeError("TOML documents must be tables (dicts)")
        path.write_bytes(tomli_w.dumps(document).encode("utf-8"))
        return
    parser = (
        document
        if isinstance(document, configparser.ConfigParser)
        else _ini_from_mapping(document)
    )
    with path.open("w", encoding="utf-8") as handle:
        parser.write(handle)


def _lookup(document: Any, keys: tuple[str, ...], default: Any) -> Any:
    if isinstance(document, configparser.ConfigParser):
        return _ini_lookup(document, keys, default)
    current = document
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
            continue
        if isinstance(current, list) and key.isdigit():
            index = int(key)
            if 0 <= index < len(current):
                current = current[index]
                continue
        return default
    return current


def _assign(document: Any, keys: tuple[str, ...], value: Any, fmt: FileFormat) -> Any:
    if fmt == "ini":
        parser = (
            document
            if isinstance(document, configparser.ConfigParser)
            else _ini_from_mapping(document)
        )
        _ini_assign(parser, keys, value)
        return parser
    if not keys:
        return value
    if not isinstance(document, (dict, list)):
        document = [] if keys[0].isdigit() else {}
    current: Any = document
    for index, key in enumerate(keys[:-1]):
        current = _ensure_child(current, key, keys[index + 1])
    _put(current, keys[-1], value)
    return document


def _wants_list(next_key: str) -> bool:
    return next_key.isdigit()


def _ensure_child(container: Any, key: str, next_key: str) -> Any:
    wanted: Any = [] if _wants_list(next_key) else {}
    if isinstance(container, dict):
        child = container.get(key)
        if type(child) is not type(wanted):
            child = wanted
            container[key] = child
        return child
    if isinstance(container, list) and key.isdigit():
        index = int(key)
        _extend_list(container, index)
        child = container[index]
        if type(child) is not type(wanted):
            child = wanted
            container[index] = child
        return child
    raise TypeError(f"cannot descend into {type(container).__name__} with key {key!r}")


def _put(container: Any, key: str, value: Any) -> None:
    if isinstance(container, dict):
        container[key] = value
        return
    if isinstance(container, list) and key.isdigit():
        index = int(key)
        _extend_list(container, index)
        container[index] = value
        return
    raise TypeError(f"cannot set key {key!r} on {type(container).__name__}")


def _extend_list(container: list[Any], index: int) -> None:
    if index < 0:
        raise ValueError(f"negative list index {index} is not supported")
    while len(container) <= index:
        container.append(None)


def _ini_lookup(
    parser: configparser.ConfigParser, keys: tuple[str, ...], default: Any
) -> Any:
    if not keys:
        return _ini_as_dict(parser)
    section = keys[0]
    if len(keys) == 1:
        if parser.has_section(section):
            return {
                option: _decode_ini(raw) for option, raw in parser.items(section)
            }
        if parser.has_option("DEFAULT", section):
            return _decode_ini(parser.get("DEFAULT", section))
        return default
    option = ".".join(keys[1:])
    if parser.has_option(section, option):
        return _decode_ini(parser.get(section, option))
    return default


def _ini_assign(parser: configparser.ConfigParser, keys: tuple[str, ...], value: Any) -> None:
    if not keys:
        raise ValueError("INI set with no key requires a mapping of sections")
    if len(keys) == 1:
        section = keys[0]
        if isinstance(value, dict):
            if not parser.has_section(section) and section != "DEFAULT":
                parser.add_section(section)
            for option, item in value.items():
                parser.set(section, str(option), _encode_ini(item))
            return
        parser.set("DEFAULT", section, _encode_ini(value))
        return
    section, option = keys[0], ".".join(keys[1:])
    if section != "DEFAULT" and not parser.has_section(section):
        parser.add_section(section)
    parser.set(section, option, _encode_ini(value))


def _ini_as_dict(parser: configparser.ConfigParser) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if parser.defaults():
        result["DEFAULT"] = {
            key: _decode_ini(raw) for key, raw in parser.defaults().items()
        }
    for section in parser.sections():
        result[section] = {
            option: _decode_ini(raw) for option, raw in parser.items(section)
        }
    return result


def _ini_from_mapping(document: Any) -> configparser.ConfigParser:
    mapping = _coerce_ini_document(document)
    parser = _new_ini_parser()
    for section, options in mapping.items():
        if not isinstance(options, dict):
            raise TypeError("INI sections must be mappings of option to value")
        if section != "DEFAULT" and not parser.has_section(section):
            parser.add_section(section)
        for option, item in options.items():
            parser.set(section, str(option), _encode_ini(item))
    return parser


def _coerce_ini_document(document: Any) -> dict[str, dict[str, Any]]:
    if isinstance(document, configparser.ConfigParser):
        return _ini_as_dict(document)
    if not isinstance(document, dict):
        raise TypeError("INI documents must be dicts of sections")
    return document


def _encode_ini(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _decode_ini(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw
