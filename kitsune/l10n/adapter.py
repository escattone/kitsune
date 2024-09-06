import re


LINK_HOOKS = re.compile(r"\[\[(Image|Video|V|Button|UI|Include|I|Template|T):.*?\]\]")
LINKS = re.compile(
    r"\[\[(?!Image:|Video:|V:|Button:|UI:|Include:|I:|Template:|T:)(?P<name>.+?)\]\]"
)


def protect_link(mo):
    name = mo.group("name")
    parts = name.split("|", 1)
    if (len(parts) < 2) or not parts[1]:
        # If there's no translatable text, protect the entire Wiki link syntax.
        return f'<div class="notranslate sumo-adapter">{mo.group(0)}</div>'

    title, text = parts
    return (
        f'<div class="notranslate sumo-adapter">[[{title}|</div>'
        f'{text}<div class="notranslate sumo-adapter">]]</div>'
    )


def adapt_for_translation(content):
    result = LINK_HOOKS.sub(r'<div class="notranslate sumo-adapter">\g<0></div>', content)
    result = LINKS.sub(protect_link, result)
    return result


def adapt_for_insertion(content):
    pass
