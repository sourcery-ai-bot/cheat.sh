"""
Very naive search implementation. Just a placeholder.

Exports:

    find_answer_by_keyword()

It should be implemented on the adapter basis:

    1. adapter.search(keyword) returns list of matching answers
        * maybe with some initial weight
    2. ranking is done
    3. sorted results are returned
    4. eage page are cut by keyword
    5. results are paginated

Configuration parameters:

    search.limit
"""

import re

from config import CONFIG
from routing import get_answer_dict, get_topics_list

def _limited_entry():
    return {
        'topic_type': 'LIMITED',
        "topic": "LIMITED",
        'answer': f"LIMITED TO {CONFIG['search.limit']} ANSWERS",
        'format': "code",
    }

def _parse_options(options):
    """Parse search options string into optiond_dict
    """

    if options is None:
        return {}

    return {
        'insensitive': 'i' in options,
        'word_boundaries': 'b' in options,
        'recursive': 'r' in options,
    }

def match(paragraph, keyword, options=None, options_dict=None):
    """Search for each keyword from `keywords` in `page`
    and if all of them are found, return `True`.
    Otherwise return `False`.

    Several keywords can be joined together using ~
    For example: ~ssh~passphrase
    """

    if keyword is None:
        return True

    keywords = keyword.split('~') if '~' in keyword else [keyword]
    if options_dict is None:
        options_dict = _parse_options(options)

    for kwrd in keywords:
        if not kwrd:
            continue

        regex = re.escape(kwrd)
        if options_dict["word_boundaries"]:
            regex = r"\b%s\b" % kwrd

        if options_dict["insensitive"]:
            if not re.search(regex, paragraph, re.IGNORECASE):
                return False
        elif not re.search(regex, paragraph):
            return False
    return True

def find_answers_by_keyword(directory, keyword, options="", request_options=None):
    """
    Search in the whole tree of all cheatsheets or in its subtree `directory`
    by `keyword`
    """

    options_dict = _parse_options(options)

    answers_found = []
    for topic in get_topics_list(skip_internal=True, skip_dirs=True):

        if not topic.startswith(directory):
            continue

        subtopic = topic[len(directory):]
        if not options_dict["recursive"] and '/' in subtopic:
            continue

        answer_dict = get_answer_dict(topic, request_options=request_options)
        answer_text = answer_dict.get('answer', '')
        if match(answer_text, keyword, options_dict=options_dict):
            answers_found.append(answer_dict)

        if len(answers_found) > CONFIG['search.limit']:
            answers_found.append(
                _limited_entry()
            )
            break

    return answers_found
