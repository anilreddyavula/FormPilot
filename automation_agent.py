import asyncio
import json
import time
import random
from pathlib import Path
from textwrap import dedent
from agno.agent import Agent
from agno.models.azure import AzureOpenAI
from agno.tools.mcp import MCPTools
from mcp import StdioServerParameters


class WebFormAutomationAgent:
    def __init__(self, fast_mode: bool = False, cache_store=None, target_website_url: str = None):
        self.mcp_tools = None
        self.agent = None
        self.max_retries = 3
        self.custom_rules = ""
        self.target_website_url = target_website_url or "https://aka.ms/community-activities"
        # When fast_mode is True: reduce sleeps, shorten timeouts, skip interactive prompts
        self.fast_mode = bool(fast_mode)
        # Simple adaptive rate-limit state
        self._recent_429 = 0
        self._first_429_ts = None  # type: ignore
        self._circuit_open = False
        self._rate_backoff_base = 0.5  # seconds
        if self.fast_mode:
            self._rate_backoff_base = 0.25
        # Cache for dropdown options
        self._cache = cache_store
        self._tech_options = None  # {'primary': [...], 'additional': [...]}

    def _compute_backoff(self) -> float:
        """Compute adaptive backoff with jitter and simple circuit breaker."""
        now = time.time()
        # Reset recent count if window expired
        if self._first_429_ts is None or (now - (self._first_429_ts or 0)) > 60:
            self._recent_429 = 0
            self._first_429_ts = now
            self._circuit_open = False

        self._recent_429 += 1
        # exponential backoff cap
        exponent = min(max(self._recent_429 - 1, 0), 6)
        backoff = self._rate_backoff_base * (2 ** exponent)
        # open circuit on sustained 429s
        if self._recent_429 >= 5:
            self._circuit_open = True
            backoff = max(backoff, 10.0)
        # jitter
        jitter = random.uniform(0, min(1.0, backoff * 0.1))
        return backoff + jitter

    def add_custom_rules(self, rules: str):
        """Accumulate custom rules to be appended to the agent instructions on initialize."""
        if rules:
            self.custom_rules = f"{self.custom_rules}\n\n{rules}" if self.custom_rules else rules

    async def initialize(self):
        """Initialize the Playwright MCP connection"""
        user_data_dir = str(Path(__file__).resolve().parents[1] / "playwright_data")
        server_params = StdioServerParameters(
            command="npx",
            args=["-y", "@playwright/mcp@latest", "--user-data-dir", user_data_dir],
        )

        self.mcp_tools = MCPTools(server_params=server_params, timeout_seconds=120)
        await self.mcp_tools.connect()
        
        print(f"🌐 Playwright MCP connected successfully")
        print(f"🎯 Target website: {self.target_website_url}")
        
        # On first connect, try to load cached dropdowns (tech + target audience)
        # Force fallback options for now to bypass cache issues
        print("LOG|DROPDOWN_FETCH|FORCING_FALLBACK_OPTIONS")
        self._tech_options = self._get_fallback_tech_options()
        if self._cache:
            self._cache.set('dropdowns', 'tech_options', self._tech_options)
            self._cache.save()
        print(f"LOG|DROPDOWN_FETCH|tech_primary={len((self._tech_options or {}).get('primary',[]))}|tech_additional={len((self._tech_options or {}).get('additional',[]))}")

        # Target Audience options: try to load or fetch and cache
        if self._cache:
            cached_ta = self._cache.get('dropdowns', 'target_audience')
            if cached_ta:
                self._target_audience_options = cached_ta
            else:
                try:
                    # Attempt to read from MCP dropdowns if available
                    if hasattr(self.mcp_tools, 'get_dropdown_options'):
                        dropdowns = await self.mcp_tools.get_dropdown_options()
                        ta_opts = dropdowns.get('Target Audience', []) or dropdowns.get('Audience', [])
                        # Normalize to list
                        ta_opts = list(ta_opts) if ta_opts else []
                        if ta_opts:
                            self._target_audience_options = ta_opts
                            self._cache.set('dropdowns', 'target_audience', ta_opts)
                            self._cache.save()
                        else:
                            self._target_audience_options = None
                    else:
                        self._target_audience_options = None
                except Exception:
                    self._target_audience_options = None

        base_instructions = dedent(
            f"""
                You are a specialized browser automation agent for web form activity entry.

                CRITICAL: FIRST ACTION - Navigate to target website URL: {self.target_website_url}

                CRITICAL SNAPSHOT ERROR HANDLING:
                - Use run_page_snapshot to take fresh page snapshots. Do NOT save screenshots to disk here.
                - If you get "Error: Ref not found in the current page snapshot" or similar errors:
                  1. IMMEDIATELY take a new page snapshot (run_page_snapshot)
                  2. Retry the failed operation with the new snapshot
                  3. Repeat this process for EVERY error until successful
                  4. Never give up due to snapshot errors - always take new snapshot and retry
                  5. Do NOT refresh the page for rate limits or any errors; only snapshot + retry with backoff

                RATE LIMITING AND SPEED:
                - Target throughput: 2–3 tool actions per second when possible.
                - If a rate limit (e.g., 429) is encountered, wait 500 ms and retry the action without refreshing the page.
                - Avoid unnecessary delays; proceed immediately between steps when the UI is ready.

                REQUIRED FIELDS TO FILL (ALL MANDATORY WHEN PRESENT):
                1. Category (dropdown selection). If the exact option isn't available, select the closest matching option.
                2. Main Technology (dropdown selection)
                3. Additional Technologies (multi-select): select exactly two closely related options
                4. Title (text field)
                5. Description (text area) - if the form shows a 1000 character max counter and the content exceeds it, rewrite to ~850 characters before entering
                6. Internal Notes (text area) - If empty, generate a short simple-English summary (no em-dashes). If a 1000 character max counter is exceeded, rewrite to ~850 characters
                7. Views (numeric field) - if missing, SKIP
                8. URL (URL field)
                9. Audience - select ALL appropriate options
                10. Date (calendar widget)
                11. Quantity (numeric field, default to 1)
                12. Start Date / End Date (if present) - calendar widgets

                AUTOMATION SEQUENCE:
                1. Go to the target website URL: {self.target_website_url}
                2. Ensure authentication: if already logged in, proceed; if not, perform the sign-in flow (or pause and wait for interactive login). Reuse the browser profile to keep session cookies.
                3. Take initial page snapshot (run_page_snapshot)
                4. Navigate using the website UI: look for 'Activities' menu item or 'Add Activity' button from the UI. Do NOT directly open or navigate to a hard-coded URL like '/activities/add'.
                5. Take snapshot after form loads (run_page_snapshot)
                6. For each field:
                   a. Take snapshot before interaction (run_page_snapshot)
                   b. Fill/select the field value
                   c. If error occurs, take new snapshot and retry (no refresh; 500 ms backoff on rate limit)
                   d. Verify field was filled correctly
                7. Handle calendar widgets (Date/Start/End Dates):
                   - Open calendar
                   - First select year, then month, then date
                   - Take snapshots at each step (run_page_snapshot)
                8. Validate ALL required fields are completed
                9. Take final snapshot before save (run_page_snapshot)
                10. Save the activity entry (after confirmation)

                ERROR RECOVERY PROTOCOL:
                - On ANY error: Take new snapshot immediately (run_page_snapshot)
                - Retry the failed operation up to 3 times with backoff (500 ms for rate limits)
                - Handle stale element references by taking fresh snapshots
                - For calendar widget errors: Snapshot, reopen calendar, retry (no refresh)
                - For dropdown errors: Snapshot, reopen dropdown, retry selection (no refresh)

                SNAPSHOT STRATEGY:
                - Take snapshot at start of each major operation
                - Take snapshot after any error
                - Take snapshot before and after form interactions
                - Use fresh snapshots for all element interactions

                LOGGING PROTOCOL (MANDATORY):
                - Before every MCP tool call, print a log line starting with:
                  LOG|TOOL|<tool_name>|ACTION|<action_description>
                - After the MCP action succeeds, print:
                  LOG|DONE|<action_description>
                - For every field, log before and after filling:
                  LOG|FIELD|<field_name>|VALUE|<value>|STATUS|START
                  LOG|FIELD|<field_name>|STATUS|DONE
                - Keep logs concise, one action per line.
            """
        )
        
        policy_addendum = dedent(
            """
            AUDIENCE RULE:
            - Always select ALL appropriate audience options: Developer, IT Pro, Business Decision Maker, Technical Decision Maker, Student.

            INTERNAL NOTES RULE:
            - If 'Internal Notes' is empty, generate a brief simple-English summary from 'Description' (no em-dashes), and use that.

            VIEWS RULE:
            - If 'Views' is missing or not applicable, SKIP filling that field.

            CATEGORY RULE:
            - If the exact Category is not available in the form dropdown, choose the most closely related available option without asking for user input.

            ADDITIONAL TECHNOLOGIES RULE:
            - Select exactly two closely related Additional Technology options from the available list.

            DATE FIELDS RULE:
            - Some forms have Start Date and End Date. If present, select dates by picking Year, then Month, then Date to avoid calendar mis-selection.

            FINAL REVIEW RULE:
            - After filling all fields, re-validate every field, especially date fields, to ensure the values displayed match the intended data. Do not refresh the page.
            """
        )
        
        tech_selection_rule = dedent(
            """
            TECHNOLOGY SELECTION RULE:
            - Use appropriate technology options for Main Technology and Additional Technologies based on available form options.
            - Always select exactly one Main Technology and exactly two Additional Technologies (unless the form restricts selection; then select up to two Additional).
            - Prefer technology options that are relevant to the activity content and description.
            - If exact matches are not available, use intelligent matching to find the closest available options.
            """
        )
        
        policy_addendum = policy_addendum + "\n\n" + tech_selection_rule
        full_instructions = (
            f"{base_instructions}\n\n{policy_addendum}\n\n{self.custom_rules}" if self.custom_rules else f"{base_instructions}\n\n{policy_addendum}"
        )
        
        self.agent = Agent(
            name="Web Form Automation Agent",
            model=AzureOpenAI(id="gpt-5"),
            tools=[self.mcp_tools],
            instructions=full_instructions,
            markdown=True,
        )

    async def take_snapshot_and_retry(self, operation_name: str, attempt: int):
        """Helper method to take a snapshot and handle retry logic."""
        try:
            if hasattr(self.mcp_tools, 'run_page_snapshot'):
                await self.mcp_tools.run_page_snapshot()
                print(f"LOG|SNAPSHOT|RETRY|{operation_name}|ATTEMPT|{attempt + 1}")
            else:
                print(f"LOG|SNAPSHOT|NOT_AVAILABLE|{operation_name}")
        except Exception as e:
            print(f"LOG|SNAPSHOT|ERROR|{operation_name}|{e}")

    def _generate_private_description(self, description: str) -> str:
        """Generate a simple-English internal notes from the public description with sensible ending."""
        import re as _re
        text = (description or "").replace("—", "-").replace("-", "-").strip()
        # remove URLs
        text = _re.sub(r"https?://\S+", "", text).strip()
        if not text:
            return "This is a concise internal summary for tracking this activity."
        # Cap length without ellipsis; prefer full sentence
        max_len = 400
        if len(text) > max_len:
            cutoff = text.rfind(".", 0, max_len)
            if cutoff == -1:
                cutoff = text.rfind(" ", 0, max_len)
            text = text[:cutoff if cutoff != -1 else max_len].strip()
        # Ensure ends with sentence punctuation
        if not text.endswith(('.', '!', '?')):
            text = text.rstrip('. ')
            text += '.'
        return text

    def _get_fallback_tech_options(self):
        """Get fallback technology options when MCP dropdown extraction fails"""
        return {
            'primary': [
                'Artificial Intelligence',
                'Cloud Computing', 
                'Web Development',
                'Database Technology',
                'Cybersecurity',
                'DevOps',
                'Mobile Development',
                'Data Analytics',
                'Internet of Things',
                'Blockchain',
                'Machine Learning',
                'Software Development',
                '.NET',
                'Azure',
                'Microsoft 365',
                'Power Platform'
            ],
            'additional': [
                'Python',
                'JavaScript', 
                'React',
                'Node.js',
                'C#',
                'TypeScript',
                'Azure Functions',
                'Azure App Service',
                'Power Apps',
                'Power BI',
                'SharePoint',
                'Teams',
                'Security',
                'Performance Optimization',
                'Analytics',
                'Machine Learning',
                'Network Security',
                'Compliance',
                'DevOps'
            ]
        }

    def _extract_dropdowns_from_mcp(self, dropdowns_raw: dict) -> tuple[dict | None, list | None]:
        """Scan a nested dropdowns dict returned by MCPTools and try to find
        Primary/Additional tech lists and Target Audience list.

        Returns (tech_opts, ta_opts) where tech_opts is {'primary':[], 'additional':[]} or None,
        and ta_opts is a list or None.
        """
        if not isinstance(dropdowns_raw, dict):
            return None, None

        # flatten to list of (path_keys_list, value_list)
        candidates = []

        def rec(obj, path):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    rec(v, path + [str(k)])
            elif isinstance(obj, list):
                # only accept lists of strings
                if obj and all(isinstance(i, str) for i in obj):
                    candidates.append((path, obj))

        rec(dropdowns_raw, [])

        # helper to choose target audience: look for known audience tokens
        default_aud = {"developer", "it pro", "business decision maker", "technical decision maker", "student", "author"}
        ta_choice = None
        for path, lst in candidates:
            lowered = {s.strip().lower() for s in lst}
            if lowered & default_aud:
                ta_choice = lst
                break

        # choose tech primary/additional by path keywords
        primary = None
        additional = None
        for path, lst in candidates:
            kp = " ".join(path).lower()
            if 'primary' in kp and primary is None:
                primary = lst
            if 'additional' in kp and additional is None:
                additional = lst

        # fallback: try keys mentioning 'technology' and not the ta_choice
        if (primary is None or additional is None):
            for path, lst in candidates:
                if lst is ta_choice:
                    continue
                kp = " ".join(path).lower()
                if 'technology' in kp or 'tech' in kp:
                    if primary is None:
                        primary = lst
                    elif additional is None and lst is not primary:
                        additional = lst

        tech_opts = None
        if primary or additional:
            tech_opts = {
                'primary': primary or [],
                'additional': additional or [],
            }

        return tech_opts, ta_choice

    def _choose_tech_areas(self, title: str, description: str, tech_options: dict) -> tuple[str, list]:
        """Choose one Main Technology and exactly two Additional Technology Areas from the available options.

        Strategy:
        - Score options by keyword overlap with title+description
        - Prefer options from 'primary' list for Main Technology selection when available
        - For Additional, pick top two distinct options related to Main Technology
        """
        import re

        title = (title or "").strip()
        description = (description or "").strip()
        text = f"{title} {description}".lower()
        tokens = set(re.findall(r"\w+", text))

        def score(opt: str) -> int:
            otoks = set(re.findall(r"\w+", (opt or "").lower()))
            overlap = len(tokens & otoks)
            # scoring based on token overlap
            score_val = overlap + sum(1 for t in otoks if t in tokens)
            return score_val

        primary_opts = list(tech_options.get('primary') or [])
        additional_opts = list(tech_options.get('additional') or [])

        # If no designated primary options, fall back to additional list
        if not primary_opts and additional_opts:
            primary_opts = additional_opts

        # Score primary candidates
        scored_primary = [(score(opt), opt) for opt in primary_opts]
        scored_primary.sort(reverse=True)

        primary_choice = None
        if scored_primary and scored_primary[0][0] > 0:
            primary_choice = scored_primary[0][1]
        elif primary_opts:
            # pick first available option as fallback
            primary_choice = primary_opts[0]
        else:
            # No primary options available; attempt to pick from additional_opts
            if additional_opts:
                primary_choice = additional_opts[0]
            else:
                primary_choice = None

        # Score additional candidates (prefer those in additional_opts but allow primary_opts if needed)
        candidates_for_add = additional_opts + [o for o in primary_opts if o not in additional_opts]
        scored_add = [
            (score(opt), opt)
            for opt in candidates_for_add
            if opt != primary_choice
        ]
        scored_add.sort(reverse=True)

        additional_choices = []
        for s, opt in scored_add:
            if opt not in additional_choices:
                additional_choices.append(opt)
            if len(additional_choices) >= 2:
                break

        # Fallbacks if insufficient: use available form options
        if len(additional_choices) < 2:
            pool = [o for o in (additional_opts + primary_opts) if o != primary_choice]
            for o in pool:
                if o not in additional_choices and o is not None:
                    additional_choices.append(o)
                if len(additional_choices) >= 2:
                    break

        # pad to two with empty strings
        while len(additional_choices) < 2:
            additional_choices.append("")

        # Ensure primary_choice is a string
        if primary_choice is None:
            primary_choice = ""

        # Deduplicate and keep order for additional choices
        seen = set()
        cleaned_additional = []
        for a in additional_choices:
            if not a:
                continue
            if a not in seen:
                cleaned_additional.append(a)
                seen.add(a)
            if len(cleaned_additional) >= 2:
                break
        # pad to two with empty strings
        while len(cleaned_additional) < 2:
            cleaned_additional.append("")

        return primary_choice, cleaned_additional

    async def submit_activity(self, activity_data: dict, confirm_before_save: bool = True, interactive: bool = False):
        """Submit a single activity to the web form with robust error handling and verbose logs"""

        # Validate required fields (Views optional by policy)
        required_fields = [
            'Category', 'Main Technology', 'Title', 'Description',
            'Internal Notes', 'URL', 'Date', 'Quantity', 'Audience'
        ]

        # Prepare a working copy and apply policy transforms
        activity_data = dict(activity_data)

        # Build Audience list
        if getattr(self, '_target_audience_options', None):
            target_audiences = list(self._target_audience_options)
        else:
            # Fallback default audience options
            target_audiences = [
                "Developer",
                "IT Pro", 
                "Business Decision Maker",
                "Technical Decision Maker",
                "Student",
            ]

        activity_data['Audience'] = target_audiences

        # Use cached dropdown options for technology fields if available
        if self._tech_options is None:
            # This shouldn't happen if initialization worked properly
            print("LOG|DROPDOWN_OPTIONS|MISSING_TECH_OPTIONS_FALLBACK")
            self._tech_options = self._get_fallback_tech_options()

        # Choose Main Technology and Additional Technologies using heuristic scorer
        if self._tech_options:
            primary_choice, additional_choices = self._choose_tech_areas(
                activity_data.get('Title', ''),
                activity_data.get('Description', ''),
                self._tech_options,
            )
            
            print(f"LOG|TECH_SELECTION|PRIMARY_CHOICE|{primary_choice}")
            print(f"LOG|TECH_SELECTION|ADDITIONAL_CHOICES|{additional_choices}")
            
            # Validate that the chosen primary exists in the form options
            portal_primary = [p for p in (self._tech_options.get('primary') or [])]
            portal_additional = [p for p in (self._tech_options.get('additional') or [])]

            print(f"LOG|TECH_OPTIONS|PRIMARY_COUNT|{len(portal_primary)}")
            print(f"LOG|TECH_OPTIONS|ADDITIONAL_COUNT|{len(portal_additional)}")

            def _match_choice(choice, pool):
                if not choice:
                    return ""
                # exact match first
                for p in pool:
                    if p and p.lower() == choice.lower():
                        return p
                # fallback: substring match
                for p in pool:
                    if p and choice.lower() in p.lower():
                        return p
                return ""

            validated_primary = _match_choice(primary_choice, portal_primary + portal_additional)
            print(f"LOG|TECH_VALIDATION|VALIDATED_PRIMARY|{validated_primary}")
            
            # Final fallback: if form provides no options at all, use first available
            if not validated_primary and (portal_primary or portal_additional):
                validated_primary = (portal_primary + portal_additional)[0]
                print(f"LOG|TECH_VALIDATION|FALLBACK_PRIMARY|{validated_primary}")

            # Clean additional choices: map to form strings when possible and ensure up to 2
            cleaned_additional = []
            for a in (additional_choices or []):
                if not a:
                    continue
                matched = _match_choice(a, portal_additional + portal_primary)
                if matched and matched not in cleaned_additional:
                    cleaned_additional.append(matched)
                elif a not in cleaned_additional:
                    if a in (portal_additional + portal_primary):
                        cleaned_additional.append(a)
            
            # pad to two items with empty strings
            while len(cleaned_additional) < 2:
                cleaned_additional.append("")

            activity_data['Main Technology'] = validated_primary
            activity_data['Additional Technologies'] = cleaned_additional
            
            print(f"LOG|FIELD|Main Technology|VALUE|{validated_primary}|STATUS|SELECTED")
            print(f"LOG|FIELD|Additional Technologies|VALUE|{cleaned_additional}|STATUS|SELECTED")

        # Auto-generate Internal Notes if missing/empty
        if not activity_data.get('Internal Notes'):
            activity_data['Internal Notes'] = self._generate_private_description(activity_data.get('Description', ''))

        # Validate required
        missing_fields = [field for field in required_fields if field not in activity_data or not activity_data[field]]
        if missing_fields:
            raise ValueError(f"Missing required fields: {missing_fields}")

        # Handle Views optional field
        views_present = ('Views' in activity_data and activity_data.get('Views') not in (None, ""))
        if not views_present:
            if interactive and not self.fast_mode:
                print("ℹ️ 'Views' is missing or empty. This field will be skipped. Press Enter to continue...")
                try:
                    _ = input("")
                except Exception:
                    pass

        # Local console logs of planned entries
        print("\n▶️ Planned field entries:")
        for f in ['Category','Main Technology','Additional Technologies','Title','Description','Internal Notes','URL','Audience','Date','Quantity','Start Date','End Date']:
            if f in activity_data:
                print(f"   • {f}: {activity_data.get(f)}")
        if views_present:
            print(f"   • Views: {activity_data.get('Views')}")
        else:
            print("   • Views: SKIP")
        if interactive and not self.fast_mode:
            try:
                input("\nPress Enter to proceed with browser automation...")
            except Exception:
                pass

        # Build explicit date instruction lines
        start_date = activity_data.get('Start Date')
        end_date = activity_data.get('End Date')
        date_extra = ""
        if start_date and end_date:
            date_extra = (
                f"\n           l. Start Date: \"{start_date}\" (add FIRST: open calendar -> select Year -> Month -> Date; take snapshot before and after)"
                f"\n           m. End Date: \"{end_date}\" (then add SECOND: open calendar -> select Year -> Month -> Date; take snapshot before and after)"
            )
        else:
            if start_date:
                date_extra += f"\n           l. Start Date: \"{start_date}\" (select Year -> Month -> Date)"
            if end_date:
                date_extra += f"\n           m. End Date: \"{end_date}\" (select Year -> Month -> Date)"

        views_text = str(activity_data.get('Views')) if views_present else 'SKIP'

        # Snapshot policy: when fast_mode is enabled, take minimal snapshots
        if self.fast_mode:
            snapshot_policy = dedent(
                """
                1. Take initial page snapshot (run_page_snapshot)
                2. Navigate to target website (ensure authentication)
                3. Take snapshot after navigation (run_page_snapshot)
                4. Open "Add Activity" form and take one snapshot after form opens (run_page_snapshot)
                5. For field interactions, DO NOT take a snapshot before every field; only take new snapshots on error.
                """
            )
        else:
            snapshot_policy = dedent(
                """
                1. Take initial page snapshot (run_page_snapshot)
                2. Navigate to target website (ensure authentication)
                3. Take snapshot after navigation (run_page_snapshot)
                4. Open "Add Activity" form
                5. Take snapshot after form opens (run_page_snapshot)

                For EVERY field interaction:
                 - Take snapshot before interaction (run_page_snapshot)
                 - Perform the action (fill, select, click)
                 - If you get ANY error (especially "Ref not found"), immediately:
                   * Take a new snapshot (run_page_snapshot)
                   * Retry the exact same operation
                   * Repeat until successful
                 - Do NOT refresh the page
                """
            )

        prompt = f"""
        Operate in VERBOSE LOGGING MODE and follow the LOGGING PROTOCOL.
        Submit the following activity to the web form with ROBUST ERROR HANDLING and POLICY COMPLIANCE:

        Activity Data:
        {json.dumps(activity_data, indent=2)}

        POLICY REMINDERS:
        - Audience: select ALL of these options: {json.dumps(target_audiences)}
        - Internal Notes: if derived, keep it short, simple English.
        - Views: if marked SKIP, do not fill that field.
        - Category: if the exact option is unavailable, select the closest matching available option.
        - Additional Technologies: select exactly two closely related options from the available list.
        - Do NOT refresh the page for any error; use run_page_snapshot and retry.

        CRITICAL INSTRUCTIONS:
        {snapshot_policy}

         NOTE ON NAVIGATION:
            - First navigate to: {self.target_website_url}
            - Navigate to the form via the website UI by clicking appropriate menu items then 'Add Activity'. Do NOT open or assume a direct URL. Use UI clicks and snapshots to reach the form.

        6. Fill ALL required fields in this exact order:
           a. Category: "{activity_data.get('Category', '')}" (choose closest match if needed)
           b. Main Technology: "{activity_data.get('Main Technology', '')}"
           c. Additional Technologies: {activity_data.get('Additional Technologies', [])} (choose two closely related if needed)
           d. Title: "{activity_data.get('Title', '')}"
           e. Description: "{activity_data.get('Description', '')}"
           f. Internal Notes: "{activity_data.get('Internal Notes', '')}"
           g. Views: {views_text}  # If 'SKIP', leave blank / do not fill
           h. URL: "{activity_data.get('URL', '')}"
           i. Audience: {json.dumps(target_audiences)}  # Select ALL
           j. Date: "{activity_data.get('Date', '')}" (use calendar widget: Year -> Month -> Date)
           k. Quantity: {activity_data.get('Quantity', 1)}
           {date_extra}

        7. Special handling for calendar widgets:
             - If BOTH Start Date and End Date are present: add them SEQUENTIALLY.
                 First complete the Start Date flow, then complete the End Date flow.
             - For EACH date (Start and End) perform the following exact sequence:
                 * Take snapshot before opening calendar (run_page_snapshot)
                 * Open calendar
                 * Select Year, then Month, then Date (in that order)
                 * Take snapshot after selection (run_page_snapshot)
                 * Verify the displayed date matches the intended value before proceeding to the next date

        8. Validate all fields are filled correctly (especially dates). Re-check displayed values match the intended data.
        9. Take final snapshot before save (run_page_snapshot)
        {'10. PAUSE and ask for confirmation before final save' if confirm_before_save else '10. Save the activity'}

        REMEMBER: On ANY error, take new snapshot and retry immediately. Never give up due to snapshot errors. Do not refresh the page.
        """

        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                # Time and log LLM call latency for telemetry
                start_llm = time.perf_counter()
                response = await self.agent.arun(prompt)
                llm_elapsed = time.perf_counter() - start_llm
                print(f"LOG|LLM|TIME|{llm_elapsed:.3f}s")
                
                # Print agent logs/output
                if response and getattr(response, 'content', None):
                    print("\n📜 Agent log output:")
                    print(response.content)
                return getattr(response, 'content', None)

            except Exception as e:
                error_msg = str(e)
                # Rate limit handling
                if 'rate' in error_msg.lower() or '429' in error_msg:
                    # adaptive backoff
                    backoff = self._compute_backoff()
                    print(f"⏳ Rate limit detected on attempt {attempt + 1}: backing off {backoff:.2f} s")
                    await asyncio.sleep(backoff)
                    # don't refresh; optionally take a snapshot
                    try:
                        await self.take_snapshot_and_retry("Rate limit retry", attempt)
                    except Exception:
                        pass
                    continue
                if "not found in the current page snapshot" in error_msg or "Ref" in error_msg:
                    print(f"Snapshot error on attempt {attempt + 1}: {error_msg}")
                    if attempt < max_attempts - 1:
                        print("Taking new snapshot and retrying...")
                        await self.take_snapshot_and_retry(f"Submit activity: {activity_data.get('Title', 'Unknown')}", attempt)
                        continue
                    else:
                        raise Exception(f"Failed after {max_attempts} attempts due to snapshot errors")
                else:
                    raise e

    async def close(self):
        """Safely close the MCP connection, suppressing shutdown errors."""
        try:
            if self.mcp_tools:
                try:
                    await self.mcp_tools.close()
                except Exception as e:
                    print(f"⚠️ MCP close warning: {e}")
        finally:
            self.mcp_tools = None
            self.agent = None