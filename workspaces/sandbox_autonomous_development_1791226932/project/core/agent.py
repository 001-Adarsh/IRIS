from datetime import datetime
import json
from pathlib import Path
import re

from core.development_workflow import IRISDevelopmentWorkflow
from core.executor import IRISExecutor
from core.planner import IRISPlanner
from core.researcher import WebResearcher
from core.router import AIRouter
from core.synthesizer import AISynthesizer
from core.task_state import IRISTaskState
from core.task_verifier import IRISTaskVerifier
from core.verifier import IRISVerifier
from tools.manager import ToolManager


class IRISAgent:

    MAX_TASK_STEPS = 3

    def __init__(self, project_root=None):
        self.project_root = Path(
            project_root or Path(__file__).resolve().parent.parent
        )
        self.router = AIRouter()
        self.tools = ToolManager()
        self.researcher = WebResearcher()
        self.synthesizer = AISynthesizer()

        # Resilient planner instantiation (supports standalone & agent-aware signatures)
        try:
            self.planner = IRISPlanner(agent=self)
        except TypeError:
            self.planner = IRISPlanner()

        self.executor = IRISExecutor(self.tools)
        self.verifier = IRISVerifier()
        self.development = IRISDevelopmentWorkflow(self.project_root)
        self.task_state = IRISTaskState()
        self.task_verifier = IRISTaskVerifier()
        self.chat_history = []
        self.last_objective = ""
        self.last_error_log = ""

    def _resolve_context(self, prompt: str) -> str:
        """Resolves pronouns, short interrogatives, and deliverable commands using conversation history."""
        if not self.chat_history:
            return prompt

        p_clean = prompt.lower().strip(" ?!.,:;")

        # 1. Short follow-up interrogatives (why, how, explain, tell me more)
        short_followups = {
            "why",
            "how",
            "what",
            "explain",
            "why so",
            "tell me more",
            "elaborate",
            "karan kya hai",
            "kyun",
            "what do you mean",
        }
        if p_clean in short_followups or (
            len(p_clean.split()) <= 2
            and p_clean.startswith(("why", "how", "what"))
        ):
            last_assistant_turn = next(
                (
                    t["content"]
                    for t in reversed(self.chat_history)
                    if t["role"] == "assistant"
                ),
                "",
            )
            last_user_turn = next(
                (
                    t["content"]
                    for t in reversed(self.chat_history)
                    if t["role"] == "user" and t["content"] != prompt
                ),
                "",
            )
            return (
                f"The user is asking '{prompt}' regarding the previous exchange.\n"
                f"Previous user question: '{last_user_turn}'\n"
                f"Previous assistant answer: '{last_assistant_turn[:400]}'\n"
                f"Task: Address why or how regarding that specific topic thoroughly, factually, and objectively."
            )

        # 2. Pronouns and deliverable follow-up references
        p_low = f" {prompt.lower()} "
        pronouns = [
            " he ",
            " him ",
            " his ",
            " it ",
            " that ",
            " this ",
            " about him ",
            " about her ",
            " them ",
        ]
        followup_phrases = [
            "give a doc",
            "give doc",
            "create doc",
            "make doc",
            "export to excel",
            "all details",
            "more details",
        ]

        has_pronoun = any(w in p_low for w in pronouns)
        is_followup = any(fp in p_low for fp in followup_phrases)

        if has_pronoun or is_followup:
            last_user_turn = next(
                (
                    t["content"]
                    for t in reversed(self.chat_history)
                    if t["role"] == "user"
                    and len(t["content"].strip()) > 3
                    and t["content"] != prompt
                ),
                "",
            )
            if last_user_turn:
                return f"{prompt} (Contextual subject from previous conversation turn: '{last_user_turn}')"

        return prompt

    def run(self, prompt: str):
        p_strip = prompt.strip()
        p = p_strip.lower()

        # Check exit
        if p in ["exit", "quit", "/bye", "/exit", "/quit"]:
            return "__IRIS_EXIT__"

        # -------------------------------------------------------------
        # AUTONOMOUS SELF-HEALING INTERCEPTOR
        # -------------------------------------------------------------
        clean_p = p.strip(" ?!.,:;")
        if clean_p in [
            "fix it",
            "fix this",
            "fix yourself",
            "auto fix",
            "repair",
        ]:
            last_err = (
                self.last_error_log
                or "Tool 'create_excel' rejected by executor (not registered in tools/manager.py)."
            )
            last_obj = (
                self.last_objective
                or "Create physical Excel deliverable for user request."
            )

            print("\n[IRIS Self-Healer]: Autonomous repair triggered by user...")
            repair_cmd = (
                f"Fix this bug in the repository to satisfy user objective.\n"
                f"LAST OBJECTIVE: {last_obj}\n"
                f"FAILURE ERROR: {last_err}\n"
                f"ACTION REQUIRED:\n"
                f"1. In tools/manager.py, register 'create_excel' in self.registry and self.aliases.\n"
                f"2. Implement _create_excel(self, filename='output.xlsx', content='', **kwargs) to create physical .xlsx files.\n"
                f"3. Ensure list_tools() includes 'create_excel'.\n"
                f"Output complete modified code for tools/manager.py enclosed in <<<FILE: tools/manager.py>>> and <<<END_FILE>>>."
            )
            return self.development.develop(repair_cmd, auto_deploy=True)

        # Store last objective for context recovery
        self.last_objective = prompt

        # Record user turn for persistence
        try:
            session_file = self.project_root / "data" / "last_session.txt"
            session_file.parent.mkdir(parents=True, exist_ok=True)
            with open(session_file, "a", encoding="utf-8") as f:
                f.write(f"User: {prompt}\n")
        except Exception:
            pass

        # -------------------------------------------------------------
        # 1. DETERMINISTIC SYSTEM DATE & TIME (Never search web)
        # -------------------------------------------------------------
        date_phrases = [
            "today's date",
            "todays date",
            "what is the date",
            "what is today date",
            "current date",
            "what date is it",
            "aaj ki date",
            "aaj konsi date",
        ]
        if any(dp in p for dp in date_phrases):
            now = datetime.now()
            reply = f"Today's date is **{now.strftime('%A, %B %d, %Y')}** (Local Time: {now.strftime('%I:%M %p')})."
            self._save_chat(prompt, reply)
            return reply

        time_phrases = [
            "current time",
            "what time is it",
            "what is the time",
            "time right now",
            "samay kya hua",
        ]
        if any(tp in p for tp in time_phrases):
            now = datetime.now()
            reply = f"The current time is **{now.strftime('%I:%M:%S %p')}** ({now.strftime('%A, %d %B %Y')})."
            self._save_chat(prompt, reply)
            return reply

        # -------------------------------------------------------------
        # 2. RESOLVE MULTI-TURN CONTEXT FOR PRONOUNS AND FOLLOW-UPS
        # -------------------------------------------------------------
        resolved_prompt = self._resolve_context(p_strip)

        # -------------------------------------------------------------
        # 3. AUTONOMOUS DEVELOPMENT & SELF-PATCHING (Highest Priority)
        # -------------------------------------------------------------
        dev_keywords = [
            "develop",
            "self patch",
            "patch yourself",
            "auto deploy",
            "autodeploy",
            "upgrade iris",
            "improve iris",
            "modify iris",
            "optimize yourself",
            "create a feature",
            "add a feature",
            "implement a feature",
        ]
        is_development_request = any(
            k in p for k in dev_keywords
        ) and not p.startswith(("@", "/"))

        if is_development_request:
            print(
                "\n[IRIS Agent]: Routing objective to Autonomous Development Engine..."
            )
            result = self.development.develop(p_strip, auto_deploy=True)
            if not result.get("success"):
                reply = (
                    "IRIS development could not complete safely.\n\n"
                    f"Reason: {result.get('reason', 'Unknown error')}\n"
                    f"Production modified: {result.get('production_modified', False)}"
                )
                self._save_chat(prompt, reply)
                return reply

            proposal = result.get("proposal", {})
            reply = (
                "=== IRIS AUTONOMOUS DEPLOYMENT COMPLETE ===\n\n"
                f"Objective: {result.get('objective')}\n"
                f"Status: {result.get('status')}\n"
                f"Files Changed: {', '.join(result.get('changes', []))}\n"
                f"AST & Sandbox Tests: {'PASSED' if result.get('tests', {}).get('passed') else 'FAILED'}\n"
                f"Auto-Deployed to Production: {result.get('auto_deployed', False)}\n"
                f"Proposal Record: {proposal.get('proposal_file', 'Not saved')}"
            )
            self._save_chat(prompt, reply)
            return reply

        # -------------------------------------------------------------
        # 4. UPGRADE APPROVAL & MANUAL DEPLOYMENT FALLBACK
        # -------------------------------------------------------------
        approval_triggers = [
            "approve",
            "approved",
            "approve proposal",
            "proposal approved",
            "approve last proposal",
            "deploy proposal",
            "deploy",
            "apply upgrade",
        ]
        if any(p == x or p.startswith(x) for x in approval_triggers):
            from core.development_engine import IRISDevelopmentEngine

            engine = IRISDevelopmentEngine()
            proposals = sorted(
                (self.project_root / "upgrade_proposals").glob("*.json")
            )
            if not proposals:
                return (
                    "No pending upgrade proposals found in upgrade_proposals/."
                )
            target = str(proposals[-1])
            try:
                data = json.loads(Path(target).read_text(encoding="utf-8"))
                if data.get("status") == "deployed":
                    return f"Proposal {Path(target).name} has already been approved and deployed to production."
            except Exception:
                pass
            res = engine.approve_and_deploy(target)
            reply = f"Proposal deployment finished.\nStatus: {res.get('status', 'completed')}\nProduction modified: {res.get('production_modified', False)}"
            self._save_chat(prompt, reply)
            return reply

        # -------------------------------------------------------------
        # 5. DOCUMENT / EXCEL / FILE CREATION GUARD
        # -------------------------------------------------------------
        file_keywords = [
            "doc",
            "docx",
            "file",
            "document",
            "pdf",
            "excel",
            "sheet",
            "csv",
            "write to",
            "save",
        ]
        is_doc_request = any(w in p for w in file_keywords)

        # -------------------------------------------------------------
        # 6. DIRECT IDENTITY HOOKS & CREATOR TRIGGERS
        # -------------------------------------------------------------
        if any(q in p for q in ["who i am", "who am i", "do you know me"]):
            reply = "You are Adarsh Dwivedi, my creator and lead engineer."
            self._save_chat(prompt, reply)
            return reply

        creator_triggers = [
            "who created you",
            "who is your creator",
            "who made you",
            "your owner",
            "creator linkedin",
            "who is adarsh",
        ]
        if any(t in p for t in creator_triggers) and not is_doc_request:
            reply = "I was developed by **Mr. Adarsh Dwivedi**. Connect: https://www.linkedin.com/in/adarsh-dwivedi000/"
            self._save_chat(prompt, reply)
            return reply

        # -------------------------------------------------------------
        # 7. FAST CONVERSATIONAL & IDENTITY ROUTING (Sub-Second Response)
        # -------------------------------------------------------------
        conversational_starters = [
            "hi",
            "hello",
            "hey",
            "yo",
            "ok",
            "okay",
            "thanks",
            "thank you",
            "namaste",
            "shukriya",
            "who are you",
            "what are you",
            "tell me about yourself",
            "tell me more about you",
            "introduce yourself",
            "kya haal",
            "kaise ho",
            "kaisi ho",
            "how are you",
            "what can you do",
            "help me",
        ]

        is_about_iris = any(
            phrase in p
            for phrase in [
                "who are you",
                "what are you",
                "about you",
                "about yourself",
                "your name",
                "who made you",
                "iris kon hai",
                "tum kon ho",
            ]
        )
        is_chat = any(
            p.startswith(start) or start in p for start in conversational_starters
        )

        explicit_tool_action = any(
            p.startswith(k)
            for k in [
                "search ",
                "research ",
                "run powershell",
                "run python",
                "powershell",
                "python",
            ]
        )

        if not is_doc_request and (is_about_iris or is_chat) and not explicit_tool_action:
            fast_prov = (
                self.router.providers.get("groq")
                or self.router.providers.get("gemini")
                or self.router.providers.get("openrouter")
            )
            if fast_prov and fast_prov.available:
                sys_ctx = (
                    "You are IRIS, an autonomous, evidence-based AI assistant created by Mr. Adarsh Dwivedi. "
                    "You are polite, fast, helpful, and conversational. "
                    "You are NOT the U.S. Internal Revenue Service (IRS). "
                    "Answer directly and concisely without requesting web searches or external tools."
                )
                try:
                    reply = fast_prov.generate(resolved_prompt, system=sys_ctx)
                    if reply and not str(reply).lower().startswith("groq execution error"):
                        safe_reply = self._sanitize_output(reply)
                        self._save_chat(prompt, safe_reply)
                        return safe_reply
                except Exception:
                    pass

        # -------------------------------------------------------------
        # 8. DIRECT PROVIDER ROUTING (@groq, @gemini, @ollama, @openrouter)
        # -------------------------------------------------------------
        for tag in ["groq", "gemini", "ollama", "openrouter"]:
            if p.startswith((f"@{tag}", f"/{tag}")):
                query = (
                    p_strip.split(" ", 1)[-1].strip()
                    if " " in p_strip
                    else ""
                )
                prov = self.router.providers.get(tag)
                reply = (
                    prov.generate(query)
                    if (prov and prov.available)
                    else f"{tag.capitalize()} provider not available."
                )
                self._save_chat(prompt, reply)
                return reply

        # -------------------------------------------------------------
        # 9. MULTI-MODEL COUNCIL / COMPARISON
        # -------------------------------------------------------------
        comparison_triggers = [
            "compare all",
            "compare responses",
            "comparing all",
            "compare groq",
            "compare gemini",
            "compare ollama",
            "compare openrouter",
            "@council",
            "@compare",
        ]
        if any(trig in p for trig in comparison_triggers):
            print("\n[IRIS AI Council: Multi-Model Consensus Engine]")
            target_prompt = resolved_prompt
            for trig in ["@council", "@compare"]:
                target_prompt = target_prompt.replace(trig, "").strip()
            if hasattr(self, "router") and hasattr(self.router, "compare"):
                res_dict = self.router.compare(target_prompt)
                reply = (
                    res_dict.get("final", str(res_dict))
                    if isinstance(res_dict, dict)
                    else str(res_dict)
                )
                self._save_chat(prompt, reply)
                return reply

        # -------------------------------------------------------------
        # 10. EXPLICIT RESEARCH & WEB SEARCH
        # -------------------------------------------------------------
        if any(
            p.startswith(x)
            for x in [
                "research the internet",
                "research web",
                "research online",
                "research ",
            ]
        ):
            query = resolved_prompt
            for phrase in [
                "research the internet for",
                "research web for",
                "research online for",
                "research for",
                "research",
            ]:
                if query.lower().startswith(phrase):
                    query = query[len(phrase) :].strip()
                    break
            if not query:
                return "Please provide a research topic."
            research = self.researcher.research(query, max_sources=3)
            if not research.get("evidence"):
                return "IRIS could not find usable evidence."
            reply = self.synthesizer.synthesize(query, research)
            self._save_chat(prompt, reply)
            return reply

        if any(
            p.startswith(x)
            for x in [
                "search the internet",
                "search internet",
                "search online",
                "search web",
            ]
        ):
            query = resolved_prompt
            for phrase in [
                "search the internet for",
                "search internet for",
                "search online for",
                "search web for",
                "search ",
            ]:
                if query.lower().startswith(phrase):
                    query = query[len(phrase) :].strip()
                    break
            if not query:
                return "Please provide a search query."
            results = self.tools.execute(
                "search_web", query=query, max_results=5
            )
            if not results:
                return "No web results found."
            output = ["=== IRIS WEB SEARCH ==="]
            for i, result in enumerate(results, 1):
                output.append(
                    f"\n[{i}] {result.get('title', 'No Title')}\nURL: {result.get('url', '')}\nINFO: {result.get('snippet', '')}"
                )
            reply = "\n".join(output)
            self._save_chat(prompt, reply)
            return reply

        # -------------------------------------------------------------
        # 11. MEMORY HANDLING
        # -------------------------------------------------------------
        if p.startswith(("remember ", "remember:")):
            memory = prompt
            for prefix in ["remember:", "remember that", "remember"]:
                if memory.lower().startswith(prefix):
                    memory = memory[len(prefix) :].strip()
                    break
            reply = (
                self.tools.execute("remember", content=memory)
                if memory
                else "Please provide something for me to remember."
            )
            self._save_chat(prompt, str(reply))
            return reply

        if any(
            p.startswith(x)
            for x in ["what do you remember", "search memory", "recall"]
        ):
            query = prompt
            for phrase in [
                "what do you remember about",
                "what do you remember",
                "search memory",
                "recall",
            ]:
                if query.lower().startswith(phrase):
                    query = query[len(phrase) :].strip(" ?!.,:;")
                    break
            results = self.tools.execute("search_memory", query=query)
            reply = (
                "\n".join(f"- {item[1]}" for item in results)
                if results
                else "I don't have any matching memories."
            )
            self._save_chat(prompt, reply)
            return reply

        # -------------------------------------------------------------
        # 12. EXPLICIT COMMAND EXECUTION (PowerShell / Python)
        # -------------------------------------------------------------
        if p.startswith(("run powershell", "powershell")):
            cmd = prompt
            for phrase in ["run powershell", "powershell"]:
                if cmd.lower().startswith(phrase):
                    cmd = cmd[len(phrase) :].strip()
                    break
            reply = (
                self.tools.execute("run_powershell", command=cmd)
                if cmd
                else "Please provide a PowerShell command."
            )
            self._save_chat(prompt, str(reply))
            return reply

        if p.startswith(("run python", "python")):
            code = prompt
            for phrase in ["run python", "python"]:
                if code.lower().startswith(phrase):
                    code = code[len(phrase) :].strip()
                    break
            reply = (
                self.tools.execute("run_python", code=code)
                if code
                else "Please provide Python code."
            )
            self._save_chat(prompt, str(reply))
            return reply

        # -------------------------------------------------------------
        # 13. AUTONOMOUS MULTI-PHASE TASK LOOP (Planner Driven)
        # -------------------------------------------------------------
        task_id = "TASK-" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.task_state.create(task_id, resolved_prompt)
        history = []
        execution = {}

        for step in range(1, self.MAX_TASK_STEPS + 1):
            self.task_state.update(
                task_id, status="planning", step=step, attempts=step
            )
            print(f"\nIRIS Task Loop: Step {step}/{self.MAX_TASK_STEPS}")

            try:
                plan = self.planner.plan(
                    resolved_prompt, previous_attempts=history
                )
            except TypeError:
                plan = self.planner.plan(resolved_prompt)

            if isinstance(plan, list):
                selected_tools = plan
                plan = {"tools": selected_tools}
            else:
                selected_tools = (
                    plan.get("tools", []) if isinstance(plan, dict) else []
                )

            if not selected_tools:
                print("IRIS Planner selected no executable tools.")
                break

            normalized_tools = []
            for t in selected_tools:
                if not isinstance(t, dict):
                    continue
                name = t.get("name") or t.get("tool") or t.get("action")
                purpose = (
                    t.get("purpose") or t.get("reason") or "Execute task"
                )
                args = (
                    t.get("arguments")
                    or t.get("args")
                    or t.get("parameters")
                    or {}
                )
                if name:
                    normalized_tools.append(
                        {"name": name, "purpose": purpose, "arguments": args}
                    )
                    print(f"- {name}: {purpose}")

            selected_tools = normalized_tools
            plan["tools"] = selected_tools

            if not selected_tools:
                print("IRIS Planner selected no executable tools.")
                break

            plan["requires_verification"] = True
            execution = self.executor.execute_plan(plan)

            if not execution.get("success"):
                err_msg = execution.get("error") or "Execution step failed."
                self.last_error_log = f"Step {step} failed: {err_msg}"
                history.append(
                    {
                        "step": step,
                        "plan": plan,
                        "execution": execution,
                        "verification": {
                            "verified": False,
                            "reason": "Execution failed.",
                        },
                    }
                )
                break

            print("IRIS Executor: Task executed.")
            task_verifications = []
            for result in execution.get("results", []):
                tool_name = result.get("tool", "")
                arguments = result.get("arguments", {})
                tv = self.task_verifier.verify(
                    resolved_prompt,
                    tool_name,
                    arguments,
                    result.get("result"),
                )
                task_verifications.append(tv)

            verification = {
                "verified": (
                    all(item.get("passed", False) for item in task_verifications)
                    if task_verifications
                    else False
                ),
                "reason": (
                    "; ".join(
                        item.get("reason", "") for item in task_verifications
                    )
                    if task_verifications
                    else "No tool results."
                ),
            }

            self.task_state.update(
                task_id,
                status=(
                    "completed" if verification["verified"] else "replanning"
                ),
                step=step,
                attempts=step,
                verification=verification,
            )
            print(f"IRIS Task Verifier: {verification.get('reason')}")

            if verification.get("verified"):
                # DELIVERABLE PROTECTION: Check if physical file creation was required
                file_tools_executed = any(
                    r.get("tool")
                    in ["create_file", "create_excel", "write_file"]
                    for r in execution.get("results", [])
                    if r.get("success")
                )

                if (
                    is_doc_request
                    and not file_tools_executed
                    and step < self.MAX_TASK_STEPS
                ):
                    first_res = execution.get("results", [{}])[0]
                    history.append(
                        {
                            "step": step,
                            "tool": first_res.get("tool", ""),
                            "arguments": first_res.get("arguments", {}),
                            "result": first_res.get("result", ""),
                            "verification": verification,
                        }
                    )
                    print(
                        f"\n[IRIS Task Loop]: Information gathered. Proceeding to Step {step + 1} to create deliverable file..."
                    )
                    continue

                output = []
                for result in execution.get("results", []):
                    if result.get("success"):
                        tool_val = result.get("result", "")
                        output.append(
                            f"=== {result.get('tool', 'tool')} RESULT ===\n{tool_val}"
                        )

                if output:
                    raw_tool_output = "\n\n".join(output)

                    chat_context = ""
                    if self.chat_history:
                        recent_turns = []
                        for turn in self.chat_history[-6:]:
                            role = turn.get("role", "user").upper()
                            content = str(turn.get("content", "")).strip()
                            if len(content) > 1000:
                                content = content[:1000] + " ... [truncated]"
                            recent_turns.append(f"{role}: {content}")

                        chat_context = (
                            "PREVIOUS CONVERSATION CONTEXT:\n"
                            + "\n".join(recent_turns)
                            + "\n\n"
                        )

                    final_val = ""
                    if hasattr(self, "synthesizer") and self.synthesizer:
                        try:
                            synth_payload = {
                                "evidence": raw_tool_output,
                                "query": resolved_prompt,
                                "history": chat_context,
                            }
                            synth_res = self.synthesizer.synthesize(
                                resolved_prompt, synth_payload
                            )

                            if isinstance(synth_res, dict):
                                answers = (
                                    synth_res.get("answers", {})
                                    if isinstance(
                                        synth_res.get("answers"), dict
                                    )
                                    else {}
                                )
                                final_val = (
                                    synth_res.get("final")
                                    or synth_res.get("groq")
                                    or synth_res.get("gemini")
                                    or synth_res.get("openrouter")
                                    or answers.get("groq")
                                    or answers.get("gemini")
                                    or answers.get("openrouter")
                                    or str(synth_res)
                                )
                            else:
                                final_val = str(synth_res)
                        except Exception as se:
                            print(
                                f"[Synthesizer Warning]: Failed to synthesize: {se}"
                            )

                    refusal_patterns = [
                        "i'm sorry, but i can't",
                        "i cannot help with that",
                        "i am unable to provide financial advice",
                        "as an ai language model",
                    ]
                    is_refusal = any(
                        pattern in final_val.lower()
                        for pattern in refusal_patterns
                    )

                    if not final_val.strip() or is_refusal:
                        if hasattr(self, "router") and hasattr(
                            self.router, "compare"
                        ):
                            try:
                                grounded_prompt = (
                                    "You are IRIS, an objective research assistant.\n"
                                    f"{chat_context}"
                                    f"Answer the user query '{resolved_prompt}' directly using this verified factual evidence:\n\n"
                                    f"{raw_tool_output[:8000]}\n\n"
                                    "RULES:\n"
                                    "1. State objective parameters, facts, numbers, and limitations.\n"
                                    "2. Never give blanket refusals when factual data is provided."
                                )
                                c_res = self.router.compare(grounded_prompt)
                                if isinstance(c_res, dict):
                                    final_val = c_res.get("final") or next(
                                        (
                                            v
                                            for v in c_res.get(
                                                "answers", {}
                                            ).values()
                                            if v
                                        ),
                                        "",
                                    )
                                elif isinstance(c_res, str):
                                    final_val = c_res
                            except Exception as route_err:
                                print(
                                    f"[Router Council Fallback Warning]: {route_err}"
                                )

                    if not final_val.strip():
                        final_val = (
                            raw_tool_output
                            if raw_tool_output.strip()
                            else "Task completed, but no printable output was generated."
                        )

                    final_val = self._sanitize_output(final_val)
                    self._save_chat(prompt, final_val)
                    return final_val

        # Exhausted Task Loop Fallback
        print("\n[IRIS Task Loop]: Fallback to conversational generation.")
        self.last_error_log = f"Task loop exhausted all {self.MAX_TASK_STEPS} steps without fulfilling: {resolved_prompt}"
        fallback_val = "I encountered an issue processing that objective."
        try:
            res = self.router.compare(resolved_prompt)
            if isinstance(res, dict):
                fallback_val = res.get("final") or next(
                    (v for v in res.get("answers", {}).values() if v),
                    fallback_val,
                )
            elif isinstance(res, str):
                fallback_val = res
        except Exception:
            fast_prov = self.router.providers.get(
                "groq"
            ) or self.router.providers.get("gemini")
            if fast_prov and fast_prov.available:
                try:
                    fallback_val = fast_prov.generate(resolved_prompt)
                except Exception:
                    pass

        fallback_val = self._sanitize_output(fallback_val)
        self._save_chat(prompt, fallback_val)
        return fallback_val

    @staticmethod
    def _sanitize_output(value: str) -> str:
        """Avoid console encoding crashes from Unicode punctuation while preserving content."""
        if value is None:
            return ""
        text = str(value)
        for old, new in {
            "\u2011": "-",
            "\u2012": "-",
            "\u2013": "-",
            "\u2014": "-",
            "\u2015": "-",
            "\u2026": "...",
            "\u00a0": " ",
            "\u2713": "OK",
            "\u2714": "OK",
            "\u2715": "X",
        }.items():
            text = text.replace(old, new)
        try:
            text.encode("cp1252")
            return text
        except UnicodeEncodeError:
            return text.encode("ascii", errors="ignore").decode("ascii")

    def _save_chat(self, user_text: str, assistant_text: str):
        """Thread-safe context persistence with automated session disk sync."""
        if not user_text or not assistant_text:
            return

        safe_user = self._sanitize_output(user_text)
        safe_assistant = self._sanitize_output(assistant_text)

        self.chat_history.append({"role": "user", "content": safe_user.strip()})
        self.chat_history.append(
            {"role": "assistant", "content": safe_assistant.strip()}
        )

        if len(self.chat_history) > 30:
            self.chat_history = self.chat_history[-30:]

        try:
            session_file = self.project_root / "data" / "last_session.txt"
            session_file.parent.mkdir(parents=True, exist_ok=True)
            with open(session_file, "a", encoding="utf-8") as f:
                f.write(
                    f"\n[USER]: {user_text.strip()}\n[IRIS]: {str(assistant_text).strip()}\n"
                )
        except Exception:
            pass