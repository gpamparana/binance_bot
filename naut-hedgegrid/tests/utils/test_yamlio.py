"""Tests for YAML I/O utilities."""

import os
from pathlib import Path

import pytest

from naut_hedgegrid.utils.yamlio import YamlIOError, read_yaml, write_yaml


def test_read_yaml_basic(tmp_path: Path) -> None:
    """Test basic YAML reading."""
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("key: value\nnumber: 42\n")

    data = read_yaml(yaml_file)

    assert data == {"key": "value", "number": 42}


def test_read_yaml_nested(tmp_path: Path) -> None:
    """Test reading nested YAML structures."""
    yaml_file = tmp_path / "nested.yaml"
    yaml_file.write_text("parent:\n  child: value\n  list:\n    - item1\n    - item2\n")

    data = read_yaml(yaml_file)

    assert data == {"parent": {"child": "value", "list": ["item1", "item2"]}}


def test_read_yaml_file_not_found(tmp_path: Path) -> None:
    """Test error when file doesn't exist."""
    yaml_file = tmp_path / "nonexistent.yaml"

    with pytest.raises(YamlIOError, match="not found"):
        read_yaml(yaml_file)


def test_read_yaml_invalid_syntax(tmp_path: Path) -> None:
    """Test error on invalid YAML syntax."""
    yaml_file = tmp_path / "invalid.yaml"
    # Use actually invalid YAML (unclosed bracket)
    yaml_file.write_text("key: [invalid\n")

    with pytest.raises(YamlIOError, match="Failed to parse"):
        read_yaml(yaml_file)


def test_read_yaml_env_var_resolution(tmp_path: Path) -> None:
    """Test environment variable resolution."""
    os.environ["TEST_VAR"] = "test_value"
    yaml_file = tmp_path / "env.yaml"
    yaml_file.write_text("key: ${TEST_VAR}\n")

    data = read_yaml(yaml_file, resolve_env=True)

    assert data == {"key": "test_value"}

    # Cleanup
    del os.environ["TEST_VAR"]


def test_read_yaml_env_var_with_default(tmp_path: Path) -> None:
    """Test environment variable with default value."""
    yaml_file = tmp_path / "env_default.yaml"
    yaml_file.write_text("key: ${NONEXISTENT_VAR:-default_value}\n")

    data = read_yaml(yaml_file, resolve_env=True)

    assert data == {"key": "default_value"}


def test_read_yaml_env_var_missing_no_default(tmp_path: Path) -> None:
    """Test error when environment variable is missing and no default."""
    yaml_file = tmp_path / "env_missing.yaml"
    yaml_file.write_text("key: ${NONEXISTENT_VAR}\n")

    with pytest.raises(YamlIOError, match="Environment variable.*not found"):
        read_yaml(yaml_file, resolve_env=True)


def test_read_yaml_no_env_resolution(tmp_path: Path) -> None:
    """Test skipping environment variable resolution."""
    yaml_file = tmp_path / "no_env.yaml"
    yaml_file.write_text("key: ${TEST_VAR}\n")

    data = read_yaml(yaml_file, resolve_env=False)

    assert data == {"key": "${TEST_VAR}"}


def test_read_yaml_nested_env_vars(tmp_path: Path) -> None:
    """Test environment variable resolution in nested structures."""
    os.environ["TEST_VAR1"] = "value1"
    os.environ["TEST_VAR2"] = "value2"

    yaml_file = tmp_path / "nested_env.yaml"
    yaml_file.write_text(
        "parent:\n  child1: ${TEST_VAR1}\n  child2: ${TEST_VAR2}\n"
        "list:\n  - ${TEST_VAR1}\n  - ${TEST_VAR2}\n"
    )

    data = read_yaml(yaml_file, resolve_env=True)

    assert data == {
        "parent": {"child1": "value1", "child2": "value2"},
        "list": ["value1", "value2"],
    }

    # Cleanup
    del os.environ["TEST_VAR1"]
    del os.environ["TEST_VAR2"]


def test_write_yaml_basic(tmp_path: Path) -> None:
    """Test basic YAML writing."""
    yaml_file = tmp_path / "write.yaml"
    data = {"key": "value", "number": 42}

    write_yaml(yaml_file, data)

    # Verify file was written
    assert yaml_file.exists()

    # Verify content
    content = yaml_file.read_text()
    assert "key: value" in content
    assert "number: 42" in content


def test_write_yaml_nested(tmp_path: Path) -> None:
    """Test writing nested structures."""
    yaml_file = tmp_path / "nested_write.yaml"
    data = {"parent": {"child": "value", "list": ["item1", "item2"]}}

    write_yaml(yaml_file, data)

    # Read back and verify
    read_data = read_yaml(yaml_file, resolve_env=False)
    assert read_data == data


def test_write_yaml_create_dirs(tmp_path: Path) -> None:
    """Test creating parent directories."""
    yaml_file = tmp_path / "subdir" / "nested" / "file.yaml"
    data = {"key": "value"}

    write_yaml(yaml_file, data, create_dirs=True)

    assert yaml_file.exists()
    assert read_yaml(yaml_file, resolve_env=False) == data


def test_write_yaml_round_trip(tmp_path: Path) -> None:
    """Test write -> read round trip."""
    yaml_file = tmp_path / "roundtrip.yaml"
    original_data = {
        "string": "value",
        "number": 42,
        "float": 3.14,
        "boolean": True,
        "null": None,
        "list": [1, 2, 3],
        "nested": {"key": "value"},
    }

    write_yaml(yaml_file, original_data)
    read_data = read_yaml(yaml_file, resolve_env=False)

    assert read_data == original_data
