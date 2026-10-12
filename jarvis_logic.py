# jarvis_logic.py
"""
Tiny wrapper so other front-ends can use the same Jarvis brain as main.py.

It delegates to ``main.handle_command``. Callers that have authenticated the user
should pass a ``CommandContext`` describing them; without one the request is
treated as untrusted, so risk-checked actions (shutdown, restart, cache cleanup,
clearing reminders) are refused while ordinary commands keep working.
"""

from ultron.security.policy import CommandContext


def handle_command(text: str, ctx: CommandContext = None) -> str:
    """Take plain text and return the Jarvis reply as text."""
    try:
        import main  # imported lazily: main pulls in optional integrations

        return main.handle_command(text, ctx or CommandContext.untrusted("jarvis_logic"))
    except Exception as e:
        print("jarvis_logic error:", type(e).__name__)
        return "Sorry, something went wrong in my brain."
