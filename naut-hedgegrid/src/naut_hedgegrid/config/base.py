"""Base configuration loader with YAML and Pydantic support."""

from pathlib import Path
from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel, ValidationError

from naut_hedgegrid.utils.yamlio import YamlIOError, read_yaml, write_yaml


class ConfigError(Exception):
    """Raised when configuration loading or validation fails."""


T = TypeVar("T", bound=BaseModel)


class BaseYamlConfigLoader:
    """
    Base class for loading YAML configurations with Pydantic validation.

    This class provides a convenient pattern for loading YAML files into
    validated Pydantic models with helpful error messages.

    Example:
        ```python
        class MyConfigLoader(BaseYamlConfigLoader):
            model_class = MyConfig

        config = MyConfigLoader.load("config.yaml")
        ```

    """

    model_class: ClassVar[type[BaseModel]]

    @classmethod
    def load(cls, path: Path | str, *, resolve_env: bool = True) -> BaseModel:
        """
        Load and validate a YAML configuration file.

        Args:
            path: Path to YAML configuration file
            resolve_env: Whether to resolve environment variables

        Returns:
            Validated configuration model instance

        Raises:
            ConfigError: If file cannot be loaded or validation fails

        """
        if not hasattr(cls, "model_class"):
            msg = f"{cls.__name__} must define a 'model_class' attribute"
            raise ConfigError(msg)

        path_obj = Path(path)

        # Load YAML
        try:
            data = read_yaml(path_obj, resolve_env=resolve_env)
        except YamlIOError as e:
            msg = f"Failed to load YAML from {path_obj}: {e}"
            raise ConfigError(msg) from e

        # Validate with Pydantic
        try:
            return cls.model_class.model_validate(data)
        except ValidationError as e:
            # Create helpful error message
            error_lines = [f"Configuration validation failed for {path_obj}:", ""]

            for error in e.errors():
                loc = " -> ".join(str(x) for x in error["loc"])
                msg_text = error["msg"]
                error_type = error["type"]
                error_lines.append(f"  • Field: {loc}")
                error_lines.append(f"    Error: {msg_text} ({error_type})")
                error_lines.append("")

            error_message = "\n".join(error_lines)
            raise ConfigError(error_message) from e

    @classmethod
    def load_dict(cls, data: dict[str, Any]) -> BaseModel:
        """
        Load and validate configuration from a dictionary.

        Args:
            data: Configuration data

        Returns:
            Validated configuration model instance

        Raises:
            ConfigError: If validation fails

        """
        if not hasattr(cls, "model_class"):
            msg = f"{cls.__name__} must define a 'model_class' attribute"
            raise ConfigError(msg)

        try:
            return cls.model_class.model_validate(data)
        except ValidationError as e:
            error_lines = ["Configuration validation failed:", ""]

            for error in e.errors():
                loc = " -> ".join(str(x) for x in error["loc"])
                msg_text = error["msg"]
                error_type = error["type"]
                error_lines.append(f"  • Field: {loc}")
                error_lines.append(f"    Error: {msg_text} ({error_type})")
                error_lines.append("")

            error_message = "\n".join(error_lines)
            raise ConfigError(error_message) from e

    @classmethod
    def save(cls, config: BaseModel, path: Path | str) -> None:
        """
        Save a configuration to a YAML file.

        Args:
            config: Configuration model instance to save
            path: Path to save YAML file

        Raises:
            ConfigError: If file cannot be saved

        """
        path_obj = Path(path)

        try:
            # Convert Pydantic model to dict
            data = config.model_dump(mode="python", exclude_none=True)

            # Write to YAML
            write_yaml(path_obj, data)
        except (YamlIOError, Exception) as e:
            msg = f"Failed to save configuration to {path_obj}: {e}"
            raise ConfigError(msg) from e
