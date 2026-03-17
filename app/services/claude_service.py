import anthropic
from app.core.config import get_settings
from app.models.schemas import AnalysisResponse


def _get_client() -> anthropic.Anthropic:
    settings = get_settings()
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


async def analyze(prompt: str, system_prompt: str = "") -> AnalysisResponse:
    settings = get_settings()
    client = _get_client()
    kwargs = {
        "model": settings.claude_model,
        "max_tokens": settings.claude_max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system_prompt:
        kwargs["system"] = system_prompt
    message = client.messages.create(**kwargs)
    text_content = next(
        (block.text for block in message.content if hasattr(block, "text")), "",
    )
    return AnalysisResponse(
        analysis=text_content,
        model=message.model,
        tokens_used=message.usage.input_tokens + message.usage.output_tokens,
    )
