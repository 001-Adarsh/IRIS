import re
import json
from pathlib import Path
from typing import List, Dict, Any
from core.router import AIRouter


class IRISPlanner:
    """Advanced AI Council-powered cognitive planning and task orchestration engine."""

    def __init__(self, agent=None):
        self.router = AIRouter()
        self.agent = agent

    def _get_conversation_context(self) -> str:
        """Retrieves conversational memory from agent runtime or persistent storage."""
        # 1. Check in-memory agent chat history
        agent_hist = (
            getattr(self, "chat_history", None)
            or getattr(self.agent, "chat_history", None)
            or getattr(self.agent, "history", None)
        )
        if agent_hist and isinstance(agent_hist, list):
            turns = []
            for t in agent_hist[-6:]:
                role = t.get("role", "speaker").upper()
                content = str(t.get("content", ""))[:400]
                if content:
                    turns.append(f"{role}: {content}")
            if turns:
                return "RECENT CHAT HISTORY:\n" + "\n".join(turns)

        # 2. Check persistent disk session
        session_file = Path.home() / "IRIS" / "data" / "last_session.txt"
        if session_file.exists():
            try:
                lines = session_file.read_text(encoding="utf-8").strip().splitlines()
                if lines:
                    return "PERSISTENT SESSION LOG:\n" + "\n".join(lines[-8:])
            except Exception:
                pass

        return "No prior conversational context."

    def analyze_intent_with_council(self, request: str, previous_attempts: list | None = None) -> Dict[str, Any]:
        """Consults the multi-model AI Council to understand prompt semantics and resolve references."""
        context = self._get_conversation_context()
        attempts_summary = ""
        if previous_attempts:
            attempts_summary = "\nPREVIOUS EXECUTION ATTEMPTS:\n" + "\n".join(
                f"- Tool: {a.get('tool') or a.get('name')} | Result preview: {str(a.get('result', ''))[:150]}"
                for a in previous_attempts[-3:]
            )

        council_intent_prompt = f"""You are the Cognitive Analysis Engine of IRIS AI Assistant.
Analyze the user's latest prompt in the context of recent chat history and task attempts.

{context}
{attempts_summary}

LATEST USER PROMPT: "{request}"

TASKS:
1. Resolve ambiguous pronouns ('he', 'him', 'his', 'it', 'that', 'they') to the exact entity from history.
2. Determine if the subject is Adarsh Dwivedi (IRIS creator/engineer), a news topic, a system task, or general knowledge.
3. Detect the desired action: 'create_document' (wants file/doc/sheet on disk), 'web_research' (wants live facts), 'system_command' (OS/PowerShell), or 'conversational_answer'.
4. Identify desired file format if any: 'docx', 'md', 'xlsx', 'txt', or 'none'.

Output ONLY valid JSON:
{{
  "disambiguated_subject": "<Exact entity name with context>",
  "is_creator_query": true/false,
  "requires_file_delivery": true/false,
  "file_format": "md|docx|xlsx|txt|none",
  "recommended_search_query": "<Precise query for search_web if research is needed, else empty>"
}}"""

        try:
            res = self.router.compare(council_intent_prompt)
            raw = res.get("final") or res.get("groq") or res.get("openrouter") or str(res)
            cleaned = re.sub(r"<think>.*?</think>", "", str(raw), flags=re.DOTALL)
            cleaned = re.sub(r"```(?:json)?\s*", "", cleaned).replace("```", "").strip()
            s, e = cleaned.find("{"), cleaned.rfind("}")
            if s != -1 and e != -1:
                return json.loads(cleaned[s:e+1])
        except Exception:
            pass

        return {
            "disambiguated_subject": request,
            "is_creator_query": any(k in request.lower() for k in ["creator", "owner", "adarsh"]),
            "requires_file_delivery": any(k in request.lower() for k in ["doc", "file", "excel", "sheet", "pdf"]),
            "file_format": "md" if "doc" in request.lower() else "none",
            "recommended_search_query": request
        }

    def build_prompt(self, request: str, previous_attempts: list | None = None) -> str:
        previous_attempts = previous_attempts or []
        history_text = ""
        if previous_attempts:
            history_text = "\nPREVIOUS ATTEMPTS IN THIS TASK:\n"
            for att in previous_attempts:
                history_text += (
                    f"Step {att.get('step')}: Tool={att.get('tool') or att.get('name')}, "
                    f"Args={att.get('arguments')}, "
                    f"Verified={att.get('verification', {}).get('verified', False)}\n"
                    f"Output snippet: {str(att.get('result', ''))[:300]}\n"
                )

        return f"""You are the Autonomous Planning Engine for IRIS.
Formulate a strictly typed JSON execution plan for the request.

USER REQUEST: {request}
{history_text}

AVAILABLE TOOLS:
- get_owner_profile: args {{}} -> Verified local identity/background of creator Adarsh Dwivedi.
- search_web: args {{"query": "<terms>", "max_results": 5}} -> Search Google/DuckDuckGo.
- read_webpage: args {{"url": "<url>"}} -> Extract full body text from a webpage.
- create_file: args {{"filename": "<path/filename>", "content": "<text>"}} -> Save file to disk.
- create_excel: args {{"filename": "<path.xlsx>", "data": [{{...}}]}} -> Generate Excel file.
- run_powershell: args {{"command": "<cmd>"}} -> Run safe diagnostic Windows PowerShell.
- run_python: args {{"code": "<code>"}} -> Run Python scripts.

RULES:
1. When a deliverable file is requested, use 'create_file' or 'create_excel'. Do not just answer in text.
2. For news or live information, step 1 is 'search_web' with headline-focused keywords.
3. If an explicit URL is provided, use 'read_webpage'.
4. Return ONLY valid JSON matching this schema:
{{
  "task_type": "research|file|computer|python|answer",
  "objective": "short description",
  "tools": [
    {{
      "name": "tool_name",
      "purpose": "why this tool is chosen",
      "arguments": {{}}
    }}
  ],
  "requires_verification": true,
  "risk": "low|medium|high"
}}"""

    def plan(self, request: str, previous_attempts: list | None = None) -> dict:
        previous_attempts = previous_attempts or []
        req_low = request.lower().strip()

        # =========================================================
        # 1. DIRECT URL HANDLING (Web Reader)
        # =========================================================
        urls = re.findall(r"https?://[^\s\)\]\>]+", request)
        already_read = any(
            (att.get("tool") == "read_webpage" or att.get("name") == "read_webpage")
            for att in previous_attempts
        )
        if urls and not already_read:
            target_url = urls[0]
            print(f"\n[IRIS Planner]: Direct URL detected -> Scheduling read_webpage for: {target_url}")
            return {
                "task_type": "research",
                "objective": f"Extract full text from {target_url}",
                "tools": [{
                    "name": "read_webpage",
                    "purpose": "Read actual article body text",
                    "arguments": {"url": target_url}
                }],
                "requires_verification": True,
                "risk": "low"
            }

        # =========================================================
        # 2. COGNITIVE INTENT ANALYSIS VIA AI COUNCIL
        # =========================================================
        intent = self.analyze_intent_with_council(request, previous_attempts)
        is_creator = intent.get("is_creator_query", False)
        requires_file = intent.get("requires_file_delivery", False)
        file_fmt = intent.get("file_format", "none")
        subject = intent.get("disambiguated_subject", request)

        # Check existing collected data in this task loop
        collected_data = []
        for att in previous_attempts:
            res = att.get("result") or ""
            if res and len(str(res)) > 25:
                collected_data.append(str(res))

        # =========================================================
        # 3. CREATOR IDENTITY & LOCAL KNOWLEDGE ENGINE
        # =========================================================
        if is_creator or any(k in req_low for k in ["who is he", "about him", "his details", "creator", "adarsh"]):
            if not collected_data:
                print(f"\n[IRIS Planner]: Recognized Creator Inquiry -> Retrieving verified local profile...")
                return {
                    "task_type": "owner_profile",
                    "objective": "Retrieve verified profile of creator Adarsh Dwivedi",
                    "tools": [{
                        "name": "get_owner_profile",
                        "purpose": "Load verified local identity profile",
                        "arguments": {}
                    }],
                    "requires_verification": True,
                    "risk": "low"
                }

            if requires_file or any(k in req_low for k in ["doc", "file", "document", "docx"]):
                combined = "\n\n".join(collected_data)
                doc_content = f"# Professional Profile: Adarsh Dwivedi\nCreator & Lead Engineer of IRIS AI Assistant\n\n## Verified Profile\n{combined}\n\n## Internal Verification\nStatus: Verified via IRIS Internal Identity Registry."
                filename = "Adarsh_Dwivedi_Profile.md" if file_fmt != "docx" else "data/Adarsh_Dwivedi_Profile.docx"

                return {
                    "task_type": "file",
                    "objective": f"Generate profile document for Adarsh Dwivedi",
                    "tools": [{
                        "name": "create_file",
                        "purpose": "Save profile report to disk",
                        "arguments": {
                            "filename": filename,
                            "content": doc_content
                        }
                    }],
                    "requires_verification": True,
                    "risk": "low"
                }

        # =========================================================
        # 4. MULTI-PHASE FILE & EXCEL GENERATION PIPELINE
        # =========================================================
        if requires_file or file_fmt in ["xlsx", "csv", "docx", "md"]:
            # Phase 1: Research if we don't have enough data
            if not collected_data:
                search_q = intent.get("recommended_search_query") or f"{subject} details facts overview"
                print(f"\n[IRIS Planner]: File requested -> Phase 1: Gathering web data for '{search_q}'")
                return {
                    "task_type": "research",
                    "objective": f"Gather web evidence for: {subject}",
                    "tools": [{
                        "name": "search_web",
                        "purpose": "Collect verified information to populate file",
                        "arguments": {"query": search_q, "max_results": 5}
                    }],
                    "requires_verification": True,
                    "risk": "low"
                }

            # Phase 2: Compile Deliverable
            evidence_text = "\n".join(collected_data)[:5000]
            if file_fmt in ["xlsx", "csv"] or "excel" in req_low or "sheet" in req_low:
                return {
                    "task_type": "file",
                    "objective": f"Generate Excel spreadsheet for: {subject}",
                    "tools": [{
                        "name": "create_excel",
                        "purpose": "Write structured spreadsheet to disk",
                        "arguments": {
                            "filename": f"data/{re.sub(r'[^a-zA-Z0-9_]', '_', subject)[:20]}_report.xlsx",
                            "data": [{"Topic": subject, "Details": evidence_text[:600]}]
                        }
                    }],
                    "requires_verification": True,
                    "risk": "low"
                }
            else:
                return {
                    "task_type": "file",
                    "objective": f"Write research document for: {subject}",
                    "tools": [{
                        "name": "create_file",
                        "purpose": "Write Markdown document to disk",
                        "arguments": {
                            "filename": f"data/{re.sub(r'[^a-zA-Z0-9_]', '_', subject)[:20]}_report.md",
                            "content": f"# Research Report: {subject}\n\n{evidence_text}"
                        }
                    }],
                    "requires_verification": True,
                    "risk": "low"
                }

        # =========================================================
        # 5. LIVE NEWS & HEADLINES OPTIMIZER
        # =========================================================
        if any(k in req_low for k in ["news", "headline", "breaking news", "current events"]):
            return {
                "task_type": "research",
                "objective": "Collect breaking news headlines from live web search",
                "tools": [{
                    "name": "search_web",
                    "purpose": "Retrieve current top headlines",
                    "arguments": {"query": "top news headlines today breaking India world Reuters AP", "max_results": 6}
                }],
                "requires_verification": True,
                "risk": "low"
            }

        # =========================================================
        # 6. GENERAL COUNCIL PLANNER (FALLBACK)
        # =========================================================
        raw_plan = self.router.compare(self.build_prompt(request, previous_attempts))
        plan_text = raw_plan.get("final") or raw_plan.get("groq") or raw_plan.get("openrouter") or str(raw_plan)

        try:
            cleaned = re.sub(r"<think>.*?</think>", "", plan_text, flags=re.DOTALL)
            cleaned = re.sub(r"```(?:json)?\s*", "", cleaned).replace("```", "").strip()
            s, e = cleaned.find("{"), cleaned.rfind("}")
            if s != -1 and e != -1:
                data = json.loads(cleaned[s:e+1])
                if isinstance(data, dict):
                    data.setdefault("tools", [])
                    data.setdefault("requires_verification", True)
                    data.setdefault("risk", "low")
                    return data
        except Exception:
            pass

        return {
            "task_type": "research" if any(w in req_low for w in ["search", "find", "who", "what", "where"]) else "answer",
            "objective": request,
            "tools": [{
                "name": "search_web",
                "purpose": "Gather information from web",
                "arguments": {"query": request}
            }] if any(w in req_low for w in ["search", "find", "who", "what", "where"]) else [],
            "requires_verification": True,
            "risk": "low"
        }