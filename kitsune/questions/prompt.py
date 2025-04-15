import json

from langchain_core.messages import HumanMessage, SystemMessage

from kitsune.sumo import TOPICS_BY_PRODUCT
from kitsune.questions.models import Question


BEGIN_RESULT = "<<<begin-result>>>"
END_RESULT = "<<<end-result>>>"


def get_hierarchical_title(topic):
    result = topic.title
    while topic.parent:
        result = f"{topic.parent.title} > {result}"
        topic = topic.parent
    return result


def get_eligible_topics(product, format="json"):
    """
    Returns the available topics, as a string in the specified format,
    based on the given product.
    """
    result = dict(topics=TOPICS_BY_PRODUCT[product.title])
    # if format == "yaml":
    #     return yaml.dump(result, sort_keys=False)
    return json.dumps(result)


def get_example_questions(product, max_examples_per_topic=3):
    """
    Returns questions and their assigned topics as a string.
    """
    topic_titles = []

    for t1 in TOPICS_BY_PRODUCT[product.title]:
        topic_titles.append(t1["title"])
        for t2 in t1.get("subtopics", ()):
            topic_titles.append(t2["title"])
            for t3 in t2.get("subtopics", ()):
                topic_titles.append(t3["title"])

    result = ["---BEGIN EXAMPLE QUESTIONS---"]

    example_count = 0
    for topic_title in topic_titles:
        questions = (
            Question.objects.filter(
                is_spam=False,
                is_archived=False,
                solution__isnull=False,
                product=product,
                topic__title=topic_title,
            )
            .select_related("topic")
            .order_by("-num_votes_past_week")[:max_examples_per_topic]
        )
        for question in questions:
            example_count += 1
            result.append(f"---BEGIN EXAMPLE {example_count}---")
            result.append(f"Question: ```{question.content}```")
            result.append(f'Assigned Topic: "{get_hierarchical_title(question.topic)}"')
            result.append(f"---END EXAMPLE {example_count}---")

    result.append("---END EXAMPLE QUESTIONS---")

    return "\n".join(result)


def get_prompt(question):
    """
    Returns a list of LLM messages (a "system" message plus a "user" message) for
    use as the prompt when invoking a response from an LLM model.
    """
    return [
        SystemMessage(""),
        HumanMessage(""),
    ]


def get_result(text):
    """
    Given the text of the LLM response, extracts and returns the actual translation.
    """
    return text.split(BEGIN_RESULT)[-1].split(END_RESULT)[0]
