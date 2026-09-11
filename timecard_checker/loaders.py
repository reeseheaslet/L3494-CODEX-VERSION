from pathlib import Path
from typing import Union, IO, Any
import pandas as pd


def load_csv(file_input: Union[str, Path, IO[Any]]) -> pd.DataFrame:
    """
    Generic CSV loader.

    Supports:
    - local file paths (for local testing / CLI use)
    - file-like objects from Flask uploads (for web use)

    Why this exists:
    - keeps file reading in one place
    - makes future validation easier
    - gives us one reusable way to load source files
    """
    if isinstance(file_input, (str, Path)):
        path = Path(file_input)

        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        return pd.read_csv(path)

    # Otherwise assume it is a file-like object, such as
    # Flask's uploaded file from request.files["..."]
    return pd.read_csv(file_input)


def load_executime_csv(file_input: Union[str, Path, IO[Any]]) -> pd.DataFrame:
    """
    Load an executime CSV.

    For now this only reads the file.
    Later this is where executime-specific cleanup will go.
    """
    return load_csv(file_input)


def load_firstdue_csv(file_input: Union[str, Path, IO[Any]]) -> pd.DataFrame:
    """
    Load a First Due CSV.

    For now this only reads the file.
    Later this is where First Due-specific cleanup will go.
    """
    return load_csv(file_input)
