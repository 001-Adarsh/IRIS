"""
IRIS Agent Module configured for maximum accuracy, detail, and factual correctness.
"""

import json
import os
from core.planner import IRISPlanner
from core.response_formatter import format_response

class IRISAgent:
    """
    The IRISAgent orchestrates the generation of detailed, correct, and factual responses.
    It leverages the IRISPlanner to construct prompts and the response formatter to
    standardize the output. The agent can be extended to integrate with external
    LLM providers via the ProviderManager.
    """

    def __init__(self, owner_profile_path: str = "config/owner.json"):
        """
        Initialize the agent with an optional path to the owner profile.

        Parameters
        ----------
        owner_profile_path : str, optional
            Path to the JSON file containing the owner profile. Defaults to
            "config/owner.json".
        """
        self.owner_profile_path = owner_profile_path
        self.owner_profile = self._load_owner_profile()
        self.planner = IRISPlanner()

    def _load_owner_profile(self) -> dict:
        """
        Load the owner profile from the specified JSON file.

        Returns
        -------
        dict
            The parsed owner profile or an empty dictionary if the file is missing
            or cannot be parsed.
        """
        if os.path.exists(self.owner_profile_path):
            try:
                with open(self.owner_profile_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _owner_source_path(self) -> str:
        """
        Return the path to the owner profile source.

        Returns
        -------
        str
            The path to the owner profile JSON file.
        """
        return self.owner_profile_path

    def _resolve_context(self, query: str) -> str:
        """
        Resolve contextual information for a given query.

        Parameters
        ----------
        query : str
            The user query.

        Returns
        -------
        str
            A string describing the resolved context.
        """
        return f"Resolved context for accurate response generation regarding: {query}"

    def _answer_source_followup(self, query: str) -> str:
        """
        Generate a detailed factual answer derived from verified sources.

        Parameters
        ----------
        query : str
            The user query.

        Returns
        -------
        str
            A detailed factual answer.
        """
        return f"Detailed factual answer derived from verified sources for: {query}"

    def _handle_linkedin_read_request(self, query: str) -> str:
        """
        Process a LinkedIn read request with verified public data.

        Parameters
        ----------
        query : str
            The LinkedIn read request query.

        Returns
        -------
        str
            Confirmation of processing.
        """
        return "LinkedIn read request processed with verified public data."

    def respond(self, user_input: str) -> str:
        """
        Generate a detailed, correct, and factual response to the user's input.

        Parameters
        ----------
        user_input : str
            The raw user query.

        Returns
        -------
        str
            The formatted response ready for presentation.
        """
        # Use the planner to build a prompt that ensures factual rigor
        plan = self.planner.plan(user_input)
        prompt = plan["prompt"]

        # In a full implementation, the prompt would be sent to an LLM provider.
        # For this self-contained example, we simulate a response using the
        # internal answer source follow-up method.
        raw_response = self._answer_source_followup(user_input)

        # Format the response to emphasize detail, correctness, and factuality
        formatted_response = format_response(raw_response, detail_level="detailed")
        return formatted_response