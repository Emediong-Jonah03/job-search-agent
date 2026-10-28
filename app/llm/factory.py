from app.config import get_settings
from app.llm.providers import create_provider


def get_llm_provider():
    return create_provider(get_settings())

