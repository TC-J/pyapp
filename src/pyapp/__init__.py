from pathlib import Path
from typing import Any, Dict

import yaml
from importlib import resources

from .runtime import AppStorageManager
from .program import Program, ProcessResult


def load_package_file(
        module: str, 
        relpath: str,
) -> str:
    """Load package-file on target at runtime.

    Load package resource files using module-path and the relative-path to file. Must configure
    pyproject.toml to package this resource-module.
    """
    return (resources.files(module) / relpath).read_text("utf-8")


def extract_from_yaml(utf8_data: str, safe: bool) -> Dict[str, str]:
    """Extract YAML from file into a dictionary.
    """
    return yaml.safe_load(utf8_data) if safe else yaml.load(utf8_data, Loader=yaml.FullLoader)


def persist_to_yaml(data: dict, filepath: str | Path):
    """Persist dictionary to file in YAML format."""
    path: Path = Path(filepath)

    if not path.exists():
        path.touch()

    with path.open("w+") as file:
        file.write(yaml.dump(data))


def main():
    app_store = AppStorageManager(
        "tgzoomr", 
        base_data_dir="test-app", 
        base_config_dir="test-app"
    )

    app_store["settings.yaml:cluser_name"] = "a"

    cat = Program("cat")
    result = cat.run("-n", "test-app/settings.yaml")
    print(result.stdout)