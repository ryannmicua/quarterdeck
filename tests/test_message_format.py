import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import quarterdeck  # noqa: E402

render = quarterdeck.message_text_html


class MessageFormatTests(unittest.TestCase):
    def test_paragraphs_split_on_blank_lines(self):
        self.assertEqual(render("One.\n\nTwo."), '<div class="record rich"><p>One.</p><p>Two.</p></div>')

    def test_bullets_and_numbers(self):
        out = render("- a\n* b\n\n1. c\n2. d")
        self.assertIn("<ul><li>a</li><li>b</li></ul>", out)
        self.assertIn("<ol><li>c</li><li>d</li></ol>", out)

    def test_list_directly_after_a_line_of_text(self):
        self.assertIn("<p>Intro:</p><ul><li>a</li></ul>", render("Intro:\n- a"))

    def test_bold_lead_word(self):
        self.assertIn("<li><strong>Yes</strong> do it</li>", render("- **Yes** do it"))

    def test_bare_urls_link(self):
        out = render("See https://example.com/a.\n\nDone")
        self.assertIn('<a href="https://example.com/a" target="_blank" rel="noopener noreferrer">', out)

    def test_html_is_escaped(self):
        out = render("<script>x</script> **<b>y</b>**\n\n- <img src=x onerror=y>\n\n# not a heading")
        self.assertNotIn("<script", out)
        self.assertNotIn("<img", out)
        self.assertNotIn("<h1", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertIn("# not a heading", out)

    def test_plain_message_renders_as_before(self):
        self.assertEqual(render("Line one\nLine two"), '<p class="record">Line one\nLine two</p>')
        self.assertEqual(render("Merge"), '<p class="record">Merge</p>')

    def test_unmatched_markers_stay_plain(self):
        self.assertEqual(render("2 ** 3 and **open"), '<p class="record">2 ** 3 and **open</p>')
        self.assertEqual(render("-not a bullet"), '<p class="record">-not a bullet</p>')

    def test_item_continuation_line(self):
        self.assertIn("<li>a\nmore</li>", render("- a\n  more\n- b"))

    def test_numbered_list_continues_across_blank_lines(self):
        out = render("Questions:\n\n1. a\n\n2. b\n\n3. c")
        self.assertEqual(out, '<div class="record rich"><p>Questions:</p><ol><li>a</li><li>b</li><li>c</li></ol></div>')

    def test_numbered_list_resumes_at_its_written_number(self):
        out = render("1. a\n2. b\nmid\n3. c")
        self.assertEqual(out, '<div class="record rich"><ol><li>a</li><li>b</li></ol><p>mid</p><ol start="3"><li>c</li></ol></div>')

    def test_paragraph_after_blank_line_ends_the_list(self):
        self.assertEqual(render("- a\n\n  later"), '<div class="record rich"><ul><li>a</li></ul><p>  later</p></div>')

if __name__ == "__main__":
    unittest.main()
