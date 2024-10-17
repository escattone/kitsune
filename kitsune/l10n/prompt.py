from django.conf.settings import LOCALES
from django.template.loader import render_to_string
from langchain_core.messages import HumanMessage, SystemMessage


def get_language_in_english(locale):
    """
    Returns the name of the locale in English, for example "it" returns "Italian"
    and "pt-BR" returns "Portuguese (Brazilian)".
    """
    return LOCALES[locale].english


def get_example(rev, target_locale):
    """
    Returns a dictionary containing the most recent translation of the given
    revision's document, or None if no approved translation exists.
    """
    trans_doc = rev.document.translated_to(target_locale)

    if not (
        trans_doc
        and (example_target := trans_doc.current_revision)
        and (example_source := example_target.based_on)
    ):
        return None

    return dict(
        source_text=example_source.content,
        target_text=example_target.content,
    )


def get_messages(source_text, source_locale, target_locale, example=None):
    """
    A generic function for returning a list of LLM messages ("syste," and "user"
    messages)
    """
    context = dict(
        example=example,
        source_text=source_text,
        source_language=get_language_in_english(source_locale),
        target_language=get_language_in_english(target_locale),
    )
    return [
        SystemMessage(content=render_to_string("llm_system_message.txt", context)),
        HumanMessage(content=render_to_string("llm_user_message.txt", context)),
    ]


def get_prompt(rev, target_locale):
    """
    Returns a list of LLM messages (a "system" message plus a "user" message) for
    use as the prompt when invoking a response from an LLM model. The messages
    comprise a request to translate the given revision's content into the language
    of the given target locale.
    """
    return get_messages(
        source_text=rev.content,
        source_locale=rev.document.locale,
        target_locale=target_locale,
        example=get_example(rev, target_locale),
    )
