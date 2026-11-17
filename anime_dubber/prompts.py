"""Shared prompt formatting for local translation and review models."""


def render_prompt(tokenizer, prompt: str):
    if getattr(tokenizer, "chat_template", None) is None:
        return prompt
    messages = [{"role": "user", "content": prompt}]
    try:
        return tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, enable_thinking=False)
    except TypeError:
        # Older tokenizers do not accept the thinking option.
        return tokenizer.apply_chat_template(messages, add_generation_prompt=True)
