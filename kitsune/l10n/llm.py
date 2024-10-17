from langchain_openai import ChatOpenAI

from kitsune.l10n.prompt import get_prompt


llm = ChatOpenAI(
    model="gpt-4o-2024-08-06",
    temperature=0,
    max_tokens=None,
    max_retries=2,
    timeout=None,
)


def get_localization(rev, target_locale):
    result = llm.invoke(get_prompt(rev, target_locale))
    # Parse the result.content to get the translated content
    # I think we should we record the result metadata.
    return result
