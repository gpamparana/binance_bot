"""YAML I/O utilities with environment variable resolution."""

import os
import re
from pathlib import Path
from typing import Any

import yaml


class YamlIOError(Exception):
    """Raised when YAML I/O operations fail."""


def _resolve_env_vars(data: Any) -> Any:
    """
    Recursively resolve environment variables in YAML data.

    Supports ${VAR_NAME} and ${VAR_NAME:-default_value} syntax.

    Args:
        data: YAML data structure (dict, list, str, etc.)

    Returns:
        Data with environment variables resolved

    Raises:
        YamlIOError: If required environment variable is missing

    """
    if isinstance(data, dict):
        return {key: _resolve_env_vars(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_resolve_env_vars(item) for item in data]
    if isinstance(data, str):
        # Match ${VAR_NAME} or ${VAR_NAME:-default}
        pattern = r"\$\{([^}:]+)(?::-(.*?))?\}"

        def replacer(match: re.Match[str]) -> str:
            var_name = match.group(1)
            default_value = match.group(2)

            # Get env var or use default
            value = os.environ.get(var_name)

            if value is None:
                if default_value is not None:
                    return default_value
                msg = (
                    f"Environment variable '{var_name}' not found and no default provided. "
                    f"Set the variable or use ${{VAR_NAME:-default}} syntax."
                )
                raise YamlIOError(msg)

            return value

        return re.sub(pattern, replacer, data)

    return data


def read_yaml(path: Path | str, *, resolve_env: bool = True) -> dict[str, Any]:
    """
    Read and parse a YAML file.

    Args:
        path: Path to YAML file
        resolve_env: Whether to resolve environment variables

    Returns:
        Parsed YAML data as dictionary

    Raises:
        YamlIOError: If file cannot be read or parsed

    """
    path_obj = Path(path)

    if not path_obj.exists():
        msg = f"YAML file not found: {path_obj}"
        raise YamlIOError(msg)

    try:
        with path_obj.open("r") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        msg = f"Failed to parse YAML file {path_obj}: {e}"
        raise YamlIOError(msg) from e
    except OSError as e:
        msg = f"Failed to read YAML file {path_obj}: {e}"
        raise YamlIOError(msg) from e

    if data is None:
        return {}

    if not isinstance(data, dict):
        msg = f"Expected YAML file {path_obj} to contain a dictionary, got {type(data)}"
        raise YamlIOError(msg)

    if resolve_env:
        try:
            resolved = _resolve_env_vars(data)
            if not isinstance(resolved, dict):
                msg = f"Expected resolved data to be a dictionary, got {type(resolved)}"
                raise YamlIOError(msg)
            data = resolved
        except YamlIOError as e:
            msg = f"Failed to resolve environment variables in {path_obj}: {e}"
            raise YamlIOError(msg) from e

    return data


def write_yaml(
    path: Path | str,
    data: dict[str, Any],
    *,
    create_dirs: bool = True,
) -> None:
    """
    Write data to a YAML file.

    Args:
        path: Path to YAML file
        data: Data to write
        create_dirs: Whether to create parent directories if they don't exist

    Raises:
        YamlIOError: If file cannot be written

    """
    path_obj = Path(path)

    if create_dirs:
        path_obj.parent.mkdir(parents=True, exist_ok=True)

    try:
        with path_obj.open("w") as f:
            yaml.safe_dump(
                data,
                f,
                default_flow_style=False,
                sort_keys=False,
                indent=2,
                allow_unicode=True,
            )
    except yaml.YAMLError as e:
        msg = f"Failed to write YAML to {path_obj}: {e}"
        raise YamlIOError(msg) from e
    except OSError as e:
        msg = f"Failed to write file {path_obj}: {e}"
        raise YamlIOError(msg) from e
