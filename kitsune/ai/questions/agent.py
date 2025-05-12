from google.adk.agents import Agent, SequentialAgent

from functools import cache

from kitsune.ai.questions.prompt import get_system_instructions
from kitsune.ai.questions.tools import assign_topic, flag_as_spam
from kitsune.products.models import Product
from kitsune.questions.models import Question


@cache
def get_agents(product: Product, model: str = "gemini-2.5-flash-preview-04-17"):
    """
    Creates and returns the spam and topic agents for the given product and LLM model.
    """
    spam_agent = Agent(
        model=model,
        name="spam_agent",
        instruction=get_system_instructions("spam", product),
        tools=[flag_as_spam],
        output_key="spam_check_result",
    )
    topic_agent = Agent(
        model=model,
        name="topic_agent",
        prompt=get_system_instructions("topic", product),
        tools=[assign_topic],
        output_key="topic_classification_result",
    )

    # def stop_on_spam(
    #     tool: BaseTool, args: Dict[str, Any], tool_context: ToolContext, tool_response: Dict
    # ) -> Optional[Dict]:
    #     if tool.name == "flag_as_spam":
    #         pass

    SequentialAgent(
        name="classify_agent",
        sub_agents=[spam_agent, topic_agent],
        # after_tool_callback=stop_on_spam,
    )
    return spam_agent, topic_agent


def classify(question: Question, model_name: str = "gemini-2.5-flash-preview-04-17") -> None:
    """
    Top-level agent that manages spam and topic agents in order to first determine whether
    or not to classify a question as spam, and if not, then assign a topic to the question.
    """
    spam_agent, topic_agent = get_agents(question.product, model_name)

    # input = dict(
    #     messages=[
    #         HumanMessage(f"Question ID = {question.id}\nQuestion:\n{question.content}"),
    #     ]
    # )

    # spam_response = spam_agent.invoke(input)

    # if not has_tool_been_called("flag_as_spam", spam_response):
    #     topic_agent.invoke(input)
