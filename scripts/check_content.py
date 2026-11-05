"""Checks an article or LinkedIn post against the content guide before publishing.

Usage: python scripts/check_content.py article my_article.md
       python scripts/check_content.py linkedin my_post.txt
"""
import pathlib
import re
import sys

BANNED_WORD = "hack" + "athon"  # any mention disqualifies the content, hashtags included
ARTICLE_LINKS = ["github.com/vectorize-io/hindsight", "hindsight.vectorize.io", "vectorize.io/what-is-agent-memory"]
TITLE_HYPE = ["revolutionary", "unlock", "smart", "ai-powered"]
LINKEDIN_TAGS = {"#aiagents", "#ai", "#hindsight", "#agentmemory", "#aimemory", "#llm"}


def check_article(text: str) -> list[str]:
    problems, words = [], len(re.findall(r"\b[\w'’-]+\b", text))
    if not 1200 <= words <= 1500:
        problems.append(f"Length is {words} words. Aim for 1,200-1,500, which fits both figures in the guide.")
    for link in ARTICLE_LINKS:
        if link not in text:
            problems.append(f"Missing required link: {link}")
    title = next((line[2:] for line in text.splitlines() if line.startswith("# ")), "")
    if not title:
        problems.append("Missing a single H1 title.")
    else:
        if "hindsight" not in title.lower():
            problems.append("The guide's title prompt asks for Hindsight in the title.")
        if len(title.split()) > 10:
            problems.append("Title is over 10 words.")
        problems += [f"Title uses a word the guide bans: {w}" for w in TITLE_HYPE if w in title.lower()]
    if "```" not in text:
        problems.append("Needs at least one real code snippet from the project.")
    if "![" not in text:
        problems.append("Needs screenshots or images. Add them here or on the publishing platform.")
    if "simulat" not in text.lower():
        problems.append("Say that the vendors and figures are simulated.")
    return problems


def check_linkedin(text: str) -> list[str]:
    problems, lines = [], [line for line in text.strip().splitlines() if line.strip()]
    if len(text.strip()) >= 800:
        problems.append(f"Post is {len(text.strip())} characters. It must be under 800.")
    tags = re.findall(r"#\w+", text)
    last_tags = re.findall(r"#\w+", lines[-1]) if lines else []
    if not 3 <= len(tags) <= 5:
        problems.append(f"Has {len(tags)} hashtags. It needs 3-5.")
    if len(tags) != len(last_tags):
        problems.append("Hashtags must be on the last line only.")
    unknown = [t for t in tags if t.lower() not in LINKEDIN_TAGS]
    if unknown:
        problems.append(f"Not in the guide's hashtag list: {', '.join(unknown)}")
    if any("#" in line or "http" in line for line in lines[:2]):
        problems.append("No hashtags or links in the first 2 lines.")
    if "github.com" not in text:
        problems.append("The project's GitHub link must be in the main post.")
    if "hindsight" not in text.lower():
        problems.append("Mention Hindsight positively in the body.")
    return problems


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in {"article", "linkedin"}:
        sys.exit(__doc__)
    text = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
    problems = []
    if BANNED_WORD in text.lower():
        problems.append(f'DISQUALIFYING: remove the word "{BANNED_WORD}".')
    problems += check_article(text) if sys.argv[1] == "article" else check_linkedin(text)
    for problem in problems:
        print("-", problem)
    print("Looks good." if not problems else f"{len(problems)} problem(s).")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
