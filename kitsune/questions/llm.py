from functools import cache

from kitsune.questions.prompt import get_prompt, get_result


def is_openai_model(model_name):
    """
    Returns whether or not the given model name is an OpenAI model.
    """
    return any(model_name.startswith(prefix) for prefix in ("gpt-", "chatgpt-", "o1-", "o3-"))


@cache
def get_chat_model(model_name):
    """
    Returns a LangChain chat model instance based on the given model name.
    """
    kwargs = dict(
        model=model_name,
        temperature=0,
        max_tokens=None,
        max_retries=2,
        timeout=120,
    )

    if is_openai_model(model_name):
        from langchain_openai import ChatOpenAI as ChatAI
    else:
        from langchain_google_vertexai import ChatVertexAI as ChatAI

    return ChatAI(**kwargs)


def assign_topic(model_name, question):
    """
    Invokes the LLM specified by the given model name to select an appropriate
    topic for the given question.
    """
    prompt = get_prompt(question)
    llm = get_chat_model(model_name)
    response = llm.invoke(prompt)
    return get_result(response.content)
