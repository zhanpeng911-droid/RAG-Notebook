import yaml

def load_config(
        config_path: str,
        encoding: str = 'utf-8'
) -> dict:
    with open(config_path, 'r', encoding=encoding) as file:
        config = yaml.safe_load(file)
    return config


