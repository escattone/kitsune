import re

from playwright.sync_api import Page
from playwright_tests.core.basepage import BasePage


class ProductSupportForum(BasePage):
    # Ask the Community button
    __ask_the_community_button = "//a[contains(text() ,'Ask the Community')]"

    # Showing questions tagged section
    __showing_questions_tagged_tag = "//div[@id='tagged']//a[@class='tag']"
    __show_all_questions_option = "//a[@class='show-all']"

    # Question status filters
    __all_question_status_filters = "//ul[@class='tabs--list subtopics']/li[@class='tabs--item']"

    # Side navbar filter options
    __side_navbar_filter_options = "//ul[@class='sidebar-nav--list']//a"
    __topic_dropdown_selected_option = "//select[@id='products-topics-dropdown']/option[@selected]"

    # Question list
    __all_question_list_tags = "//li[@class='tag']"
    __all_listed_articles = "//div[@id='questions-list']//article"

    def __init__(self, page: Page):
        super().__init__(page)

    # Ask the Community actions
    def _click_on_the_ask_the_community_button(self):
        super()._click(self.__ask_the_community_button)

    def _get_selected_topic_option(self) -> str:
        option = super()._get_text_of_element(self.__topic_dropdown_selected_option)
        return re.sub(r'\s+', ' ', option).strip()

    # Showing Questions Tagged section actions
    def _get_text_of_selected_tag_filter_option(self) -> str:
        return super()._get_text_of_element(self.__showing_questions_tagged_tag)

    def _click_on_the_show_all_questions_option(self):
        super()._click(self.__show_all_questions_option)

    # Question list actions
    def _get_all_question_list_tags(self, question_id: str) -> list[str]:
        question_tags = super()._get_text_of_elements(f"//article[@id='{question_id}']//"
                                                      f"li[@class='tag']")
        return question_tags

    def _extract_question_ids(self) -> list[str]:
        elements = super()._get_elements_locators(self.__all_listed_articles)
        id_values = []
        for element in elements:
            id_values.append(
                super()._get_element_attribute_value(
                    element, attribute="id"
                )
            )
        return id_values
