from jinja2 import Environment, FileSystemLoader

from tools import spanish


def get_jinja2_env(templates_dir: str) -> Environment:
    """Get a Jinja2 environment with standard settings."""
    env = Environment(
        loader=FileSystemLoader(templates_dir),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # serves every exporter that builds its templates here in Spanish when the
    # overlay is on; a no-op otherwise
    spanish.translate_environment(env)
    return env
