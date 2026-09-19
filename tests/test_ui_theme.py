from unittest.mock import patch

from scripts import ui_theme


def test_card_label_escapa_html():
    with patch.object(ui_theme.st, "markdown") as md:
        ui_theme.card_label('<script>alert(1)</script>')
    html_out = md.call_args.args[0]
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out
