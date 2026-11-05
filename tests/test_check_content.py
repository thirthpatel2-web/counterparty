import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_content", ROOT / "scripts" / "check_content.py")
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)

POST = """My buyer agent stopped believing vendors.
It only took a little memory.

Hindsight agent memory made the difference.
Code: https://github.com/example/counterparty
#AIAgents #Hindsight #LLM"""


def test_good_linkedin_post_passes():
    assert cc.check_linkedin(POST) == []


def test_linkedin_problems_are_caught():
    assert any("under 800" in p for p in cc.check_linkedin(POST + "x" * 800))
    assert any("last line" in p for p in cc.check_linkedin("#AI first\nsecond\nHindsight github.com\n#AI #LLM #Hindsight"))


def test_article_problems_are_caught():
    problems = " ".join(cc.check_article("# Exploring an unlock\n\nshort"))
    assert "Length" in problems and "Missing required link" in problems and "code snippet" in problems


def test_public_repo_never_contains_the_banned_word():
    """The README and code are linked from every post, so keep them clean too."""
    skip = {".venv", "__pycache__", "runs", ".pytest_cache"}
    for path in ROOT.rglob("*"):
        if path.is_file() and not skip & set(path.parts) and path.suffix in {".py", ".md", ".html", ".yaml", ".txt"}:
            assert cc.BANNED_WORD not in path.read_text(encoding="utf-8").lower(), path
