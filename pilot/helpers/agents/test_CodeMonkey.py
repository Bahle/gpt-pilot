from unittest.mock import patch, MagicMock, call
from os.path import normpath, sep
import pytest


from helpers.agents.CodeMonkey import CodeMonkey
from const.function_calls import GET_FILE_TO_MODIFY

@pytest.mark.parametrize(
    ("content", "expected_blocks"),
    [
        ("", []),
        ("no code blocks here", []),
        ("one\n```\ncode block\n```\nwithout a language tag", ["code block"]),
        ("one\n```python\ncode block\n```\nwith a language tag", ["code block"]),
        ("two\n```python\ncode\n```\n```\nblocks\n```", ["code", "blocks"]),
    ]
)
def test_get_code_blocks(content, expected_blocks):
    code_monkey = CodeMonkey(None, None)
    assert code_monkey.get_code_blocks(content) == expected_blocks


@pytest.mark.parametrize(
    ("haystack", "needle", "result", "error"),
    [
        ### Oneliner old blocks ###
        # Simple match
        ("first\nsecond\nthird", "second", "first\n@@NEW@@\nthird", None),
        # No match
        ("first\nsecond\nthird", "fourth", None, "not found"),
        # Too many matches on the same indentation level
        ("line\nline", "line", None, "found more than once"),
        # Match, replacement should be indented
        ("first\n    second\nthird", "second", "first\n    @@NEW@@\nthird", None),
        # Too many matches, on different indentation levels
        ("line\n  line", "line", None, "found more than once"),

        ### Multiline old blocks ###
        # Simple match
        ("first\nsecond\nthird", "second\nthird", "first\n@@NEW@@", None),
        # No match
        ("first\nsecond\nthird", "second\n  third", None, "not found"),
        # Too many matches on the same indentation level
        ("a\nb\nc\nd\na\nb", "a\nb", None, "found more than once"),
        # Too many matches on different indentation levels
        ("a\nb\nc\nd\n  a\n  b", "a\nb", None, "found more than once"),
        # Match, replacement should be indented
        ("first\n  second\n  third", "second\nthird", "first\n  @@NEW@@", None),

        ### Multiline with empty lines ###
        # Simple match
        ("first\nsecond\n\nthird", "second\n\nthird", "first\n@@NEW@@", None),
        # Indented match with empty lines also indentend
        ("first\n  second\n  \n  third", "second\n\nthird", "first\n  @@NEW@@", None),
        # Indented match with empty lines not indentend
        ("first\n  second\n\n  third", "second\n\nthird", "first\n  @@NEW@@", None),
    ]
)
def test_replace(haystack, needle, result, error):
    code_monkey = CodeMonkey(None, None)
    if error:
        with pytest.raises(ValueError, match=error):
            code_monkey.replace(haystack, needle, "@@NEW@@")
    else:
        assert code_monkey.replace(haystack, needle, "@@NEW@@") == result


@patch("helpers.agents.CodeMonkey.AgentConvo")
def test_identify_file_to_change(MockAgentConvo):
    mock_convo = MockAgentConvo.return_value
    mock_convo.send_message.return_value = {"file": "file.py"}
    files = CodeMonkey(None, None).identify_file_to_change("some description", [])
    assert files == "file.py"
    mock_convo.send_message.assert_called_once_with(
        "development/identify_files_to_change.prompt",
        {
            "code_changes_description": "some description",
            "files": []
        },
        GET_FILE_TO_MODIFY
    )


def test_dedent():
    old_code = "\n".join([
        "    def foo():",
        "        print('bar')",
    ])
    new_code = "\n".join([
        "  def bar():",
        "      print('foo')",
    ])
    expected_old = "\n".join([
        "  def foo():",
        "      print('bar')",
    ])
    expected_new = "\n".join([
        "def bar():",
        "    print('foo')",
    ])
    result_old, result_new = CodeMonkey.dedent(old_code, new_code)
    assert result_old == expected_old
    assert expected_new == result_new


def test_codemonkey_simple():
    mock_project = MagicMock()
    mock_project.get_all_coded_files.return_value = [
        {
            "path": "",
            "name": "main.py",
            "content": "one to the\nfoo\nto the three to the four"
        },
    ]
    mock_project.get_full_file_path.return_value = ("", normpath("/path/to/main.py"))
    mock_convo = MagicMock()
    mock_convo.send_message.return_value = "## Change\nOld:\n```\nfoo\n```\nNew:\n```\nbar\n```\n"

    cm = CodeMonkey(mock_project, None)
    cm.implement_code_changes(
        mock_convo,
        "test",
        "Modify all references from `foo` to `bar`",
        {
            "path": sep,
            "name": "main.py",
        }
    )

    mock_project.get_all_coded_files.assert_called_once()
    mock_project.get_full_file_path.assert_called_once_with(sep, "main.py")
    mock_convo.send_message.assert_called_once_with(
        "development/implement_changes.prompt", {
        "full_output": False,
        "standalone": False,
        "code_changes_description": "Modify all references from `foo` to `bar`",
        "file_content": "one to the\nfoo\nto the three to the four",
        "file_name": "main.py",
        "files": mock_project.get_all_coded_files.return_value,
    })
    mock_project.save_file.assert_called_once_with({
        "path": sep,
        "name": "main.py",
        "content": "one to the\nbar\nto the three to the four"
    })


def test_codemonkey_retry():
    file_content = (
        "one to the\nfoo\nto the three to the four\n"
        "the rest of this file is filler so it's big enought not to "
        "trigger the full replace fallback immediately upon the first failure"
    )
    mock_project = MagicMock()
    mock_project.get_all_coded_files.return_value = [
        {
            "path": "",
            "name": "main.py",
            "content": file_content,
        },
    ]
    mock_project.get_full_file_path.return_value = ("", normpath("/path/to/main.py"))
    mock_convo = MagicMock()
    mock_convo.send_message.side_effect = [
        # Incorrect match
        "## Change\nOld:\n```\ntwo\n```\nNew:\n```\nbar\n```\n",
        # Corrected match on retry
        "Apologies, here is the corrected version. ## Change\nOld:\n```\n  foo\n```\nNew:\n```\n  bar\n```\n",
    ]

    cm = CodeMonkey(mock_project, None)
    cm.implement_code_changes(
        mock_convo,
        "test",
        "Modify all references from `foo` to `bar`",
        {
            "path": sep,
            "name": "main.py",
        }
    )

    mock_project.get_all_coded_files.assert_called_once()
    mock_project.get_full_file_path.assert_called_once_with(sep, "main.py")
    mock_convo.send_message.assert_has_calls([
        call(
            "development/implement_changes.prompt", {
                "full_output": False,
                "standalone": False,
                "code_changes_description": "Modify all references from `foo` to `bar`",
                "file_content": file_content,
                "file_name": "main.py",
                "files": mock_project.get_all_coded_files.return_value,
            }
        ),
        call(
            "utils/llm_response_error.prompt", {
                "error": (
                    "Error in change 1:\n"
                    "Old code block not found in the original file:\n```\ntwo\n```\n"
                    "Old block *MUST* contain the exact same text (including indentation, empty lines, etc.) "
                    "as the original file in order to match."
                ),
            }
        )
    ])
    mock_project.save_file.assert_called_once_with({
        "path": sep,
        "name": "main.py",
        "content": file_content.replace("foo", "bar"),
    })


def test_codemonkey_fallback():
    mock_project = MagicMock()
    mock_project.get_all_coded_files.return_value = [
        {
            "path": "",
            "name": "main.py",
            "content": "one to the\nfoo\nto the three to the four"
        },
    ]
    mock_project.get_full_file_path.return_value = ("", normpath("/path/to/main.py"))
    mock_convo = MagicMock()
    mock_convo.send_message.side_effect = [
        # Incorrect match, will cause immediate fallback because of short file
        "1 ## Change\nOld:\n```\ntwo\n```\nNew:\n```\nbar\n```\n",
        # Fallback returns entire new file
        "```\none to the\nbar\nto the three to the four\n```\n",
    ]

    cm = CodeMonkey(mock_project, None)
    cm.implement_code_changes(
        mock_convo,
        "test",
        "Modify all references from `foo` to `bar`",
        {
            "path": sep,
            "name": "main.py",
        }
    )

    mock_project.get_all_coded_files.assert_called_once()
    mock_project.get_full_file_path.assert_called_once_with(sep, "main.py")
    mock_convo.send_message.assert_has_calls([
        call(
            "development/implement_changes.prompt", {
                "full_output": False,
                "standalone": False,
                "code_changes_description": "Modify all references from `foo` to `bar`",
                "file_content": "one to the\nfoo\nto the three to the four",
                "file_name": "main.py",
                "files": mock_project.get_all_coded_files.return_value,
            }
        ),
        call(
            'development/implement_changes.prompt', {
                "full_output": True,
                "standalone": False,
                "code_changes_description": "Modify all references from `foo` to `bar`",
                "file_content": "one to the\nfoo\nto the three to the four",
                "file_name": "main.py",
                "files": mock_project.get_all_coded_files.return_value,
            }
        )
    ])
    mock_project.save_file.assert_called_once_with({
        "path": sep,
        "name": "main.py",
        "content": "one to the\nbar\nto the three to the four"
    })


@patch("helpers.agents.CodeMonkey.get_file_contents")
@patch("helpers.agents.CodeMonkey.AgentConvo")
def test_codemonkey_implement_changes_after_debugging(MockAgentConvo, mock_get_file_contents):
    """
    Test that the flow to figure out files that need to be changed
    (which happens after debugging where we only have a description of the
    changes needed, not file name).

    Also test standalone conversation (though that's not happening after debugging).
    """
    mock_project = MagicMock()
    mock_project.get_all_coded_files.return_value = []
    mock_project.get_full_file_path.return_value = ("", "/path/to/main.py")
    mock_convo = MockAgentConvo.return_value
    mock_convo.send_message.return_value = "## Change\nOld:\n```\nfoo\n```\nNew:\n```\nbar\n```\n"
    mock_get_file_contents.return_value = {
        "name": "main.py",
        "path": "",
        "content": "one to the\nfoo\nto the three to the four",
        "full_path": "/path/to/main.py",
    }

    cm = CodeMonkey(mock_project, None)
    with patch.object(cm, "identify_file_to_change") as mock_identify_file_to_change:
        mock_identify_file_to_change.return_value = "/main.py"
        cm.implement_code_changes(
            None,
            "test",
            "Modify all references from `foo` to `bar`",
            {},
        )

    MockAgentConvo.assert_called_once_with(cm)
    mock_project.get_all_coded_files.assert_called_once()
    mock_project.get_full_file_path.assert_called_once_with("/", "main.py")
    mock_convo.send_message.assert_called_once_with(
        "development/implement_changes.prompt", {
        "full_output": False,
        "standalone": True,
        "code_changes_description": "Modify all references from `foo` to `bar`",
        "file_content": "one to the\nfoo\nto the three to the four",
        "file_name": "main.py",
        "files": mock_project.get_all_coded_files.return_value,
    })
    mock_project.save_file.assert_called_once_with({
        "path": "/",
        "name": "main.py",
        "content": "one to the\nbar\nto the three to the four"
    })
