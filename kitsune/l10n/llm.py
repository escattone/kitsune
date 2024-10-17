from django.conf import settings

from kitsune.l10n.prompt import get_prompt, get_result


if any(
    settings.SUMO_L10N_LLM.startswith(prefix)
    for prefix in (
        "gpt-",
        "chatgpt-",
        "o1-",
    )
):
    from langchain_openai import ChatOpenAI as ChatAI
else:
    from langchain_google_vertexai import ChatVertexAI as ChatAI

LLM = ChatAI(
    model=settings.SUMO_L10N_LLM,
    temperature=0,
    max_tokens=None,
    max_retries=2,
    timeout=120,
)


def get_localization(doc, content_attribute, target_locale):
    prompt = get_prompt(doc, content_attribute, target_locale)
    response = LLM.invoke(prompt)
    print("---SYSTEM MESSAGE---")
    print(prompt[0].content)
    print("--------------------")
    print("----USER MESSAGE----")
    print(prompt[1].content)
    print("--------------------")
    print("-------RESULT-------")
    print(response.content)
    print("--------------------")
    return get_result(response.content)
