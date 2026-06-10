from pathlib import Path
from langchain_core.prompts import PromptTemplate

TEMPLATE_DIR = Path(__file__).parent.parent / "prompts" / "templates"


def render_prompt(template_name: str, **kwargs) -> str:
    template_path = TEMPLATE_DIR / f"{template_name}.j2"
    if not template_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {template_name}")
    with open(template_path, "r", encoding="utf-8") as f:
        template_text = f.read()
    prompt = PromptTemplate.from_template(template_text, template_format="jinja2")
    return prompt.format(**kwargs)
