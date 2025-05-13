from kitsune.llm.questions.prompt import spam_parser, spam_prompt, topic_parser, topic_prompt
from kitsune.llm.utils import get_llm
from kitsune.products.utils import get_taxonomy
from kitsune.questions.models import Question

DEFAULT_LLM_MODEL = "gemini-2.5-flash-preview-04-17"


def analyze_question(question: Question) -> tuple[bool, dict]:
    """
    Analyze a question from spam and classifies the topic.
    """
    llm = get_llm(model_name=DEFAULT_LLM_MODEL)

    product = question.product
    payload = {
        "question": question.content,
        "product": product,
        "topics": get_taxonomy(
            product, include_metadata=["description", "examples"], output_format="JSON"
        ),
    }

    spam_detection_chain = spam_prompt | llm | spam_parser
    topic_classification_chain = topic_prompt | llm | topic_parser

    def conditional_topic_classification_chain(result):
        if result["is_spam"] and (result["confidence"] >= 60):
            result.update(topic_result=None)
        else:
            result.update(topic_result=topic_classification_chain.invoke(payload))
        return result

    pipeline = spam_detection_chain | conditional_topic_classification_chain

    result = pipeline.invoke(payload)
    print(result)
    # Draft - we need to process the result
    return result["is_spam"], result["topic_result"]
