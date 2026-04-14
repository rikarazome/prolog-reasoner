"""Configuration management for prolog-reasoner."""

import subprocess

from pydantic_settings import BaseSettings, SettingsConfigDict

from prolog_reasoner.errors import ConfigurationError


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    All settings can be overridden via PROLOG_REASONER_ prefixed env vars
    or a .env file.
    """

    model_config = SettingsConfigDict(
        env_prefix="PROLOG_REASONER_",
        env_file=".env",
    )

    # LLM
    llm_provider: str = "openai"
    llm_api_key: str = ""  # Optional: only needed for translate_to_prolog
    llm_model: str = "gpt-5.4-mini"
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 30.0

    # Prolog
    swipl_path: str = "swipl"
    execution_timeout_seconds: float = 10.0

    # Logging
    log_level: str = "INFO"

    def validate_swipl(self) -> None:
        """Verify SWI-Prolog is installed and functional.

        Called once at startup. Raises ConfigurationError if SWI-Prolog
        is missing, broken, or unresponsive.
        """
        try:
            result = subprocess.run(
                [self.swipl_path, "--version"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode != 0:
                raise ConfigurationError(
                    f"SWI-Prolog returned exit code {result.returncode}.\n"
                    f"stderr: {result.stderr.decode(errors='replace')}\n"
                    f"Path: {self.swipl_path}",
                    error_code="CONFIG_001",
                )
        except (FileNotFoundError, PermissionError):
            raise ConfigurationError(
                "SWI-Prolog not found. Install from: "
                "https://www.swi-prolog.org/download/stable\n"
                f"Searched path: {self.swipl_path}\n"
                "Or set PROLOG_REASONER_SWIPL_PATH to the correct location.",
                error_code="CONFIG_001",
            )
        except subprocess.TimeoutExpired:
            raise ConfigurationError(
                f"SWI-Prolog did not respond within 5 seconds.\n"
                f"Path: {self.swipl_path}",
                error_code="CONFIG_001",
            )
