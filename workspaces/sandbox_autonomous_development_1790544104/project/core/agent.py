import json
import re
from pathlib import Path
from datetime import datetime

from core.router import AIRouter
from core.researcher import WebResearcher
from core.synthesizer import AISynthesizer
from core.planner import IRISPlanner
from core.executor import IRISExecutor
from core.verifier import IRISVerifier
from core.task_state import IRISTaskState
from core.task_verifier import IRISTaskVerifier
from core.development_workflow import IRISDevelopmentWorkflow
from tools.manager import ToolManager


class IRISAgent:

    MAX_TASK_STEPS = 3

    def __init__(self):
        self.router = AIRouter()
        self.tools = ToolManager()
        self.researcher = WebResearcher()
        self.synthesizer = AISynthesizer()
        # Bind agent instance to planner so it accesses live conversation context
        self.planner = IRISPlanner(agent=self)
        self.executor = IRISExecutor(self.tools)
        self.verifier = IRISVerifier()
        self.development = IRISDevelopmentWorkflow()
        self.task_state = IRISTaskState()
        self.task_verifier = IRISTaskVerifier()
        self.chat_history = []

    def _resolve_context(self, prompt: str) -> str:
        """Resolves pronouns and follow-up commands using conversational history."""
        if not self.chat_history:
            return prompt

        p_low = f" {prompt.lower()} "
        pronouns = [" he ", " him ", " his ", " it ", " that ", " this ", " about him ", " about her ", " them "]
        has_pronoun = any(w in p_low for w in pronouns)

        # Follow-up triggers like "give a doc", "export it", "details"
        followup_phrases = ["give a doc", "give doc", "create doc", "make doc", "export to excel", "all details", "more details"]
        is_followup = any(fp in p_low for fp in followup_phrases)

        if has_pronoun or is_followup:
            last_user_turn = next(
                (t["content"] for t in reversed(self.chat_history)
                 if t["role"] == "user" and len(t["content"].strip()) > 3 and t["content"] != prompt),
                ""
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

        # Record user turn for persistence
        try:
            p_s = Path.home() / "IRIS" / "data" / "last_session.txt"
            p_s.parent.mkdir(parents=True, exist_ok=True)
            with open(p_s, "a", encoding="utf-8") as f:
                f.write(f"User: {prompt}\n")
        except Exception:
            pass

        # -------------------------------------------------------------
        # 1. DETERMINISTIC SYSTEM DATE & TIME (Never search web)
        # -------------------------------------------------------------
        date_phrases = ["today's date", "todays date", "what is the date", "what is today date", "current date", "what date is it", "aaj ki date", "aaj konsi date"]
        if any(dp in p for dp in date_phrases):
            now = datetime.now()
            reply = f"Today's date is **{now.strftime('%A, %B %d, %Y')}** (Local Time: {now.strftime('%I:%M %p')})."
            self._save_chat(prompt, reply)
            return reply

        time_phrases = ["current time", "what time is it", "what is the time", "time right now", "samay kya hua"]
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
            "develop", "self patch", "patch yourself", "auto deploy", "autodeploy",
            "upgrade iris", "improve iris", "modify iris", "fix yourself", "optimize yourself",
            "create a feature", "add a feature", "implement a feature"
        ]
        is_development_request = any(k in p for k in dev_keywords) and not p.startswith(("@", "/"))

        if is_development_request:
            print("\n[IRIS Agent]: Routing objective to Autonomous Development Engine...")
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
            "approve", "approved", "approve proposal", "proposal approved",
            "approve last proposal", "deploy proposal", "deploy", "apply upgrade"
        ]
        if any(p == x or p.startswith(x) for x in approval_triggers):
            from core.development_engine import IRISDevelopmentEngine
            engine = IRISDevelopmentEngine()
            proposals = sorted(Path("upgrade_proposals").glob("*.json"))
            if not proposals:
                return "No pending upgrade proposals found in upgrade_proposals/."
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
        file_keywords = ["doc", "docx", "file", "document", "pdf", "excel", "sheet", "csv", "write to", "save"]
        is_doc_request = any(w in p for w in file_keywords)

        # -------------------------------------------------------------
        # 6. DIRECT IDENTITY HOOKS & CREATOR TRIGGERS
        # -------------------------------------------------------------
        if any(q in p for q in ["who i am", "who am i", "do you know me"]):
            reply = "You are Adarsh Dwivedi, my creator and lead engineer."
            self._save_chat(prompt, reply)
            return reply

        creator_triggers = [
            "who created you", "who is your creator", "who made you",
            "your owner", "creator linkedin", "who is adarsh"
        ]
        if any(t in p for t in creator_triggers) and not is_doc_request:
            reply = "I was developed by **Mr. Adarsh Dwivedi**. Connect: https://www.linkedin.com/in/adarsh-dwivedi000/"
            self._save_chat(prompt, reply)
            return reply

        # -------------------------------------------------------------
        # 7. FAST CONVERSATIONAL & HINDI/HINGLISH BYPASS
        # -------------------------------------------------------------
        low_input = p.strip(" /?!.,")
        conversational_triggers = [
            'feel good', 'feeling', 'how are you', 'what can you do',
            'hello', 'hi', 'hey', 'yo', 'ok', 'okay', 'got it', 'sure', 'thanks', 'thank you',
            'kaisi ho', 'kaise ho', 'kya haal', 'namaste', 'shukriya', 'theek ho',
            'know hindi', 'hindi aati', 'hindi bol'
        ]

        if not is_doc_request and (any(t in low_input for t in conversational_triggers) or low_input in ["hi", "hello", "hey"]):
            fast_prov = self.router.providers.get('groq') or self.router.providers.get('openrouter')
            if fast_prov and fast_prov.available:
                sys_ctx = (
                    "You are IRIS, an autonomous AI assistant created by Adarsh Dwivedi. "
                    "You are fluent in both English and Hindi. Respond directly, politely, "
                    "and conversationally without unnecessary technical overhead."
                )
                try:
                    reply = fast_prov.generate(resolved_prompt, system=sys_ctx)
                    if reply and not str(reply).lower().startswith("groq execution error"):
                        self._save_chat(prompt, str(reply))
                        return str(reply)
                except Exception:
                    pass

        # -------------------------------------------------------------
        # 8. DIRECT PROVIDER ROUTING (@groq, @gemini, @ollama, @openrouter)
        # -------------------------------------------------------------
        for tag in ["groq", "gemini", "ollama", "openrouter"]:
            if p.startswith((f"@{tag}", f"/{tag}")):
                query = p_strip.split(" ", 1)[-1].strip() if " " in p_strip else ""
                prov = self.router.providers.get(tag)
                reply = prov.generate(query) if (prov and prov.available) else f"{tag.capitalize()} provider not available."
                self._save_chat(prompt, reply)
                return reply

        # -------------------------------------------------------------
        # 9. MULTI-MODEL COUNCIL / COMPARISON
        # -------------------------------------------------------------
        comparison_triggers = [
            "compare all", "compare responses", "comparing all", "compare groq",
            "compare gemini", "compare ollama", "compare openrouter", "@council", "@compare"
        ]
        if any(trig in p for trig in comparison_triggers):
            print("\n[IRIS AI Council: Multi-Model Consensus Engine]")
            target_prompt = resolved_prompt
            for trig in ["@council", "@compare"]:
                target_prompt = target_prompt.replace(trig, "").strip()
            if hasattr(self, "router") and hasattr(self.router, "compare"):
                res_dict = self.router.compare(target_prompt)
                reply = res_dict.get("final", str(res_dict)) if isinstance(res_dict, dict) else str(res_dict)
                self._save_chat(prompt, reply)
                return reply

        # -------------------------------------------------------------
        # 10. EXPLICIT RESEARCH & WEB SEARCH
        # -------------------------------------------------------------
        if any(p.startswith(x) for x in ["research the internet", "research web", "research online", "research "]):
            query = resolved_prompt
            for phrase in ["research the internet for", "research web for", "research online for", "research for", "research"]:
                if query.lower().startswith(phrase):
                    query = query[len(phrase):].strip()
                    break
            if not query:
                return "Please provide a research topic."
            research = self.researcher.research(query, max_sources=3)
            if not research.get("evidence"):
                return "IRIS could not find usable evidence."
            reply = self.synthesizer.synthesize(query, research)
            self._save_chat(prompt, reply)
            return reply

        if any(p.startswith(x) for x in ["search the internet", "search internet", "search online", "search web"]):
            query = resolved_prompt
            for phrase in ["search the internet for", "search internet for", "search online for", "search web for", "search "]:
                if query.lower().startswith(phrase):
                    query = query[len(phrase):].strip()
                    break
            if not query:
                return "Please provide a search query."
            results = self.tools.execute("search_web", query=query, max_results=5)
            if not results:
                return "No web results found."
            output = ["=== IRIS WEB SEARCH ==="]
            for i, result in enumerate(results, 1):
                output.append(f"\n[{i}] {result.get('title', 'No Title')}\nURL: {result.get('url', '')}\nINFO: {result.get('snippet', '')}")
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
                    memory = memory[len(prefix):].strip()
                    break
            reply = self.tools.execute("remember", content=memory) if memory else "Please provide something for me to remember."
            self._save_chat(prompt, str(reply))
            return reply

        if any(p.startswith(x) for x in ["what do you remember", "search memory", "recall"]):
            query = prompt
            for phrase in ["what do you remember about", "what do you remember", "search memory", "recall"]:
                if query.lower().startswith(phrase):
                    query = query[len(phrase):].strip(" ?!.,:;")
                    break
            results = self.tools.execute("search_memory", query=query)
            reply = "\n".join(f"- {item[1]}" for item in results) if results else "I don't have any matching memories."
            self._save_chat(prompt, reply)
            return reply

        # -------------------------------------------------------------
        # 12. EXPLICIT COMMAND EXECUTION (PowerShell / Python)
        # -------------------------------------------------------------
        if p.startswith(("run powershell", "powershell")):
            cmd = prompt
            for phrase in ["run powershell", "powershell"]:
                if cmd.lower().startswith(phrase):
                    cmd = cmd[len(phrase):].strip()
                    break
            reply = self.tools.execute("run_powershell", command=cmd) if cmd else "Please provide a PowerShell command."
            self._save_chat(prompt, str(reply))
            return reply

        if p.startswith(("run python", "python")):
            code = prompt
            for phrase in ["run python", "python"]:
                if code.lower().startswith(phrase):
                    code = code[len(phrase):].strip()
                    break
            reply = self.tools.execute("run_python", code=code) if code else "Please provide Python code."
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
            self.task_state.update(task_id, status="planning", step=step, attempts=step)
            print(f"\nIRIS Task Loop: Step {step}/{self.MAX_TASK_STEPS}")

            plan = self.planner.plan(resolved_prompt, previous_attempts=history)
            selected_tools = plan.get("tools", [])
            if not selected_tools:
                print("IRIS Planner selected no executable tools.")
                break

            for tool in selected_tools:
                print(f"- {tool.get('name')}: {tool.get('purpose')}")

            plan["requires_verification"] = True
            execution = self.executor.execute_plan(plan)

            if not execution.get("success"):
                history.append({
                    "step": step,
                    "plan": plan,
                    "execution": execution,
                    "verification": {"verified": False, "reason": "Execution failed."}
                })
                break

            print("IRIS Executor: Task executed.")
            task_verifications = []
            for result in execution.get("results", []):
                tool_name = result.get("tool", "")
                arguments = result.get("arguments", {})
                tv = self.task_verifier.verify(resolved_prompt, tool_name, arguments, result.get("result"))
                task_verifications.append(tv)

            verification = {
                "verified": all(item.get("passed", False) for item in task_verifications) if task_verifications else False,
                "reason": "; ".join(item.get("reason", "") for item in task_verifications) if task_verifications else "No tool results."
            }

            self.task_state.update(
                task_id,
                status="completed" if verification["verified"] else "replanning",
                step=step,
                attempts=step,
                verification=verification
            )
            print(f"IRIS Task Verifier: {verification.get('reason')}")

            if verification.get("verified"):
                # DELIVERABLE PROTECTION: Check if physical file creation is required but not yet completed
                file_tools_executed = any(
                    r.get("tool") in ["create_file", "create_excel", "write_file"]
                    for r in execution.get("results", [])
                    if r.get("success")
                )

                if is_doc_request and not file_tools_executed and step < self.MAX_TASK_STEPS:
                    first_res = execution.get("results", [{}])[0]
                    history.append({
                        "step": step,
                        "tool": first_res.get("tool", ""),
                        "arguments": first_res.get("arguments", {}),
                        "result": first_res.get("result", ""),
                        "verification": verification
                    })
                    print(f"\n[IRIS Task Loop]: Information gathered. Proceeding to Step {step + 1} to create deliverable file...")
                    continue

                output = []
                for result in execution.get("results", []):
                    if result.get("success"):
                        tool_val = result.get("result", "")
                        output.append(f"=== {result.get('tool', 'tool')} RESULT ===\n{tool_val}")

                if output:
                    raw_tool_output = "\n\n".join(output)

                    # Build conversational history snippet
                    chat_context = ""
                    if len(self.chat_history) > 0:
                        turns = [f"{t['role'].upper()}: {t['content'][:250]}" for t in self.chat_history[-4:]]
                        chat_context = "PREVIOUS CONVERSATION:\n" + "\n".join(turns) + "\n\n"

                    # Synthesizer execution
                    if hasattr(self, "synthesizer"):
                        try:
                            synth_res = self.synthesizer.synthesize(
                                resolved_prompt,
                                {'evidence': raw_tool_output, 'query': resolved_prompt, 'history': chat_context}
                            )
                            if isinstance(synth_res, dict):
                                final_val = synth_res.get('final') or synth_res.get('groq') or synth_res.get('openrouter') or str(synth_res)
                            else:
                                final_val = str(synth_res)
                            self._save_chat(prompt, final_val)
                            return final_val
                        except Exception as e:
                            print(f"[Synthesizer Error]: {e}")

                    # Fallback to direct council comparison
                    if hasattr(self, "router") and hasattr(self.router, "compare"):
                        council_prompt = f"Based on this research, answer '{resolved_prompt}':\n\n{raw_tool_output[:6000]}"
                        c_res = self.router.compare(council_prompt)
                        final_val = c_res.get('final', str(c_res)) if isinstance(c_res, dict) else str(c_res)
                        self._save_chat(prompt, final_val)
                        return final_val

                    self._save_chat(prompt, raw_tool_output)
                    return raw_tool_output

        # Final Fallback to Model Routing if Task Loop is Exhausted
        print('\n[IRIS Task Loop]: Fallback to conversational generation.')
        fallback_val = self.router.compare(resolved_prompt).get("final", "I could not complete that request.")
        self._save_chat(prompt, str(fallback_val))
        return fallback_val

    def _save_chat(self, user_text: str, assistant_text: str):
        """Saves conversational turns to memory so context is retained."""
        self.chat_history.append({"role": "user", "content": user_text})
        self.chat_history.append({"role": "assistant", "content": str(assistant_text)})
        if len(self.chat_history) > 20:
            self.chat_history = self.chat_history[-20:]