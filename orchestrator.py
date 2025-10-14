import asyncio
import json
import hashlib
import time
from pathlib import Path
from textwrap import dedent
from automation_agent import WebFormAutomationAgent
from agno.agent import Agent
from agno.models.azure import AzureOpenAI
from agno.tools.file import FileTools
from dotenv import load_dotenv

# Load environment variables from .env (current working directory, then repo root)
load_dotenv(override=False)
_env_path = (Path(__file__).resolve().parents[1] / ".env")
if _env_path.exists():
    load_dotenv(dotenv_path=_env_path, override=False)


# Simple JSON cache for generated text
class _CacheStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict = {"private_desc": {}, "rewrite850": {}}
        try:
            if self.path.exists():
                self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
        except Exception:
            pass
        # Ensure dropdowns structure exists for form options
        changed = False
        if 'dropdowns' not in self.data:
            self.data['dropdowns'] = {}
            changed = True
        # ensure technology options structure
        if 'tech_options' not in self.data['dropdowns']:
            self.data['dropdowns']['tech_options'] = {'primary': [], 'additional': []}
            changed = True
        else:
            if 'primary' not in self.data['dropdowns']['tech_options']:
                self.data['dropdowns']['tech_options']['primary'] = []
                changed = True
            if 'additional' not in self.data['dropdowns']['tech_options']:
                self.data['dropdowns']['tech_options']['additional'] = []
                changed = True
        # ensure target audience list
        if 'target_audience' not in self.data['dropdowns']:
            self.data['dropdowns']['target_audience'] = []
            changed = True
        if changed:
            try:
                self.save()
            except Exception:
                pass
    def get(self, section: str, key: str) -> str | None:
        return self.data.get(section, {}).get(key)
    def set(self, section: str, key: str, value: str):
        if section not in self.data:
            self.data[section] = {}
        self.data[section][key] = value
    def save(self):
        try:
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


class MarkdownParserAgent:
    def __init__(self):
        self.agent = Agent(
            name="Activity Data Parser",
            model=AzureOpenAI(id="gpt-5"),
            tools=[FileTools()],
            instructions=dedent(
                """
                You are a specialized agent for parsing structured activity entries from markdown files.

                Your responsibilities:
                1. Read and parse the markdown file containing activity data
                2. Extract structured activity data including ALL required fields (use NEUTRAL canonical names):
                   - Category (aka Activity Type)
                   - Main Technology (aka Primary Technology Area)
                   - Additional Technologies (array) (aka Additional Technology Areas)
                   - Title
                   - Description
                   - Internal Notes (aka Private Description)
                   - Views (integer) (aka Number of Views)
                   - URL (aka Activity URL)
                   - Audience (aka Target Audience)
                   - Date (format: YYYY-MM-DD) (aka Published Date)
                   - Quantity (default to 1 if not specified)
                3. Return activities as structured JSON objects
                4. Validate that all required fields are present
                5. Handle missing or incomplete data gracefully
                6. Ensure dates are in proper format for calendar widget
                7. OUTPUT FORMAT: Return ONLY a raw JSON array with no markdown/code fences and no extra prose.
                8. FIELD NORMALIZATION: If the markdown uses legacy names (e.g., "Activity Type", "Primary Technology Area"), map them to the neutral canonical names above in the output.
                """
            ),
            markdown=True
        )

    async def parse_activities(self, file_path: str) -> list:
        """Parse activities from markdown file"""
        response = await self.agent.arun(
            f"Parse all activity entries from {file_path} and return them as a structured JSON array using neutral canonical keys. "
            "Each activity MUST include ALL required fields: Category, Main Technology, Additional Technologies, Title, Description, "
            "Internal Notes, Views, URL, Audience, Date, and Quantity. "
            "Accept legacy synonyms (Activity Type, Primary Technology Area, Additional Technology Areas, Private Description, Number of Views, Activity URL, Target Audience, Published Date) and map them to the canonical keys. "
            "Format dates as YYYY-MM-DD for calendar widget compatibility. "
            "Return ONLY the raw JSON array (no markdown fences, no extra text)."
        )
        return response.content


class FormAutomationOrchestrator:
    def __init__(self, markdown_file_path: str, fast_mode: bool = False, target_website_url: str = None):
        self.markdown_file_path = markdown_file_path
        self._cache = _CacheStore(Path(__file__).resolve().parents[1] / ".formpilot_cache.json")
        self.parser_agent = MarkdownParserAgent()
        self.automation_agent = WebFormAutomationAgent(
            fast_mode=fast_mode, 
            cache_store=self._cache,
            target_website_url=target_website_url
        )
        self._desc_agent = None

        self.orchestrator = Agent(
            name="Form Automation Orchestrator",
            model=AzureOpenAI(id="gpt-5"),
            instructions=dedent(
                """
                You are the main orchestrator for web form activity automation.

                Your responsibilities:
                1. Coordinate between the markdown parser and browser automation agents
                2. Handle the overall workflow and error recovery
                3. Provide progress updates and status reports
                4. Manage batch processing of multiple activities
                5. Ensure data integrity throughout the process
                6. Handle user confirmations and approvals
                7. Monitor for snapshot errors and coordinate retries
                8. Validate all required fields are present before submission
                """
            ),
            markdown=True
        )

    async def _ensure_desc_agent(self):
        """Lazy-create a small agent to write internal notes/summary text with gpt-4o."""
        if self._desc_agent is None:
            self._desc_agent = Agent(
                name="Internal Notes Writer",
                model=AzureOpenAI(id="gpt-4o"),
                instructions=dedent(
                    """
                    You generate concise rewrites or internal notes/summary from a Title and Description.
                    Rules:
                    - Simple English, coherent; remove redundancy
                    - No em-dashes (—, -); use hyphen (-)
                    - No markdown, no code fences, no lists
                    - No URLs or PII
                    - When asked to fit a character limit, target the given limit and do not exceed it.
                    """
                ),
                markdown=False,
            )

    def _sanitize_private_description(self, text: str, max_len: int = 400) -> str:
        import re as _re
        t = (text or "").replace("—", "-").replace("-", "-").strip()
        # strip URLs
        t = _re.sub(r"https?://\S+", "", t).strip()
        # remove trailing ellipses-like endings
        t = _re.sub(r"(\.{3,}|…)$", "", t).strip()
        # cap length preferring sentence boundary
        if len(t) > max_len:
            cutoff = t.rfind(".", 0, max_len)
            if cutoff == -1:
                cutoff = t.rfind(" ", 0, max_len)
            t = t[:cutoff if cutoff != -1 else max_len].strip()
        # ensure ends with sentence punctuation
        if not t.endswith(('.', '!', '?')):
            t = t.rstrip('. ')
            t += '.'
        return t

    async def ensure_private_descriptions(self, activities: list[dict]):
        """Fill missing Internal Notes using cache or gpt-4o (fallback to trimmed Description)."""
        await self._ensure_desc_agent()
        dirty = False
        for act in activities:
            # Support both legacy and canonical key names
            has_internal = bool(act.get('Internal Notes'))
            has_private = bool(act.get('Private Description'))
            if not has_internal and not has_private:
                title = act.get('Title', '')
                desc = act.get('Description', '')
                key = self._hash(title, desc)
                cached = self._cache.get('private_desc', key)
                if cached:
                    act['Internal Notes'] = self._sanitize_private_description(cached)
                    print(f"🗂️ Cached Internal Notes used for '{title or 'Unknown'}'")
                    continue
                prompt = (
                    "Write internal notes/summary for form submission in simple English.\n"
                    "- One or two complete sentences.\n"
                    "- No URLs, no markdown, no lists, no ellipses.\n"
                    f"Title: {title}\n"
                    f"Description: {desc}\n"
                    "Return plain text only."
                )
                try:
                    resp = await self._desc_agent.arun(prompt)
                    text = (getattr(resp, 'content', '') or '').strip()
                except Exception:
                    text = (desc or '').replace('—','-').replace('-','-').strip()
                    if not text:
                        text = "This is a concise internal summary for tracking this activity."
                text = self._sanitize_private_description(text, 400)
                act['Internal Notes'] = text
                self._cache.set('private_desc', key, text)
                # Persist immediately so cache file reflects new private descriptions
                try:
                    self._cache.save()
                except Exception:
                    pass
                print(f"✍️ Auto-generated Internal Notes for '{title or 'Unknown'}'")
        if dirty:
            self._cache.save()

    def _hash(self, *parts: str) -> str:
        h = hashlib.sha256()
        for p in parts:
            h.update((p or "").encode("utf-8"))
            h.update(b"|")
        return h.hexdigest()

    async def enforce_char_limits(self, activities: list[dict]):
        """Ensure text fields respect character limits by rewriting to ~850 chars if exceeded; cached per original text hash."""
        await self._ensure_desc_agent()
        dirty = False
        for act in activities:
            # Description
            desc = act.get('Description')
            if isinstance(desc, str) and len(desc) > 1000:
                key = self._hash('desc850', desc)
                cached = self._cache.get('rewrite850', key)
                if cached:
                    act['Description'] = cached
                    print(f"🗂️ Cached 850-char Description used for '{act.get('Title','Unknown')}'")
                else:
                    prompt = (
                        "Rewrite the following to coherent simple English under 850 characters (hard limit).\n"
                        "No em-dashes, no markdown, no lists, no ellipses; end with a complete sentence. Plain text only.\n\n"
                        f"Text:\n{desc}"
                    )
                    try:
                        resp = await self._desc_agent.arun(prompt)
                        new_desc = (getattr(resp, 'content', '') or '').strip()
                        if len(new_desc) > 850:
                            # truncate to last sentence within limit
                            cutoff = new_desc.rfind(".", 0, 850)
                            if cutoff == -1:
                                cutoff = new_desc.rfind(" ", 0, 850)
                            new_desc = new_desc[:cutoff if cutoff != -1 else 850].strip()
                            if not new_desc.endswith(('.', '!', '?')):
                                new_desc += '.'
                    except Exception:
                        tmp = desc[:850]
                        cutoff = tmp.rfind(".")
                        if cutoff == -1:
                            cutoff = tmp.rfind(" ")
                        new_desc = tmp[:cutoff if cutoff != -1 else len(tmp)].strip()
                        if not new_desc.endswith(('.', '!', '?')):
                            new_desc += '.'
                    act['Description'] = new_desc
                    self._cache.set('rewrite850', key, new_desc)
                    # Persist immediately
                    try:
                        self._cache.save()
                    except Exception:
                        pass
                    print(f"✂️ Rewrote Description to <=850 chars for '{act.get('Title','Unknown')}'")
            # Internal Notes (aka Private Description)
            pdesc = act.get('Internal Notes') or act.get('Private Description')
            if isinstance(pdesc, str) and len(pdesc) > 1000:
                key = self._hash('pdesc850', pdesc)
                cached = self._cache.get('rewrite850', key)
                if cached:
                    act['Internal Notes'] = self._sanitize_private_description(cached, 850)
                    print(f"🗂️ Cached 850-char Internal Notes used for '{act.get('Title','Unknown')}'")
                else:
                    prompt = (
                        "Rewrite the following internal notes to <= 850 characters.\n"
                        "Simple English, no em-dashes, no markdown, no ellipses. End with a complete sentence. Plain text only.\n\n"
                        f"Text:\n{pdesc}"
                    )
                    try:
                        resp = await self._desc_agent.arun(prompt)
                        new_pdesc = (getattr(resp, 'content', '') or '').strip()
                    except Exception:
                        new_pdesc = pdesc[:850]
                    new_pdesc = self._sanitize_private_description(new_pdesc, 850)
                    act['Internal Notes'] = new_pdesc
                    self._cache.set('rewrite850', key, new_pdesc)
                    # Persist immediately
                    try:
                        self._cache.save()
                    except Exception:
                        pass
                    print(f"✂️ Rewrote Internal Notes to <=850 chars for '{act.get('Title','Unknown')}'")
        if dirty:
            self._cache.save()

    def validate_activity_data(self, activity: dict) -> tuple[bool, list]:
        """Validate required fields using neutral canonical names (legacy synonyms accepted)."""
        # Normalize keys to canonical names
        act = self._normalize_activity_keys(activity)
        required_fields = [
            'Category', 'Main Technology', 'Title', 'Description',
            'Internal Notes', 'URL', 'Date', 'Quantity', 'Audience'
        ]
        missing_fields = []
        for field in required_fields:
            if field not in act or not act[field]:
                missing_fields.append(field)
        return len(missing_fields) == 0, missing_fields

    @staticmethod
    def _normalize_activity_keys(activity: dict) -> dict:
        """Map legacy MVP-like keys to neutral canonical names used internally."""
        mapping = {
            'Activity Type': 'Category',
            'Primary Technology Area': 'Main Technology',
            'Additional Technology Areas': 'Additional Technologies',
            'Private Description': 'Internal Notes',
            'Number of Views': 'Views',
            'Activity URL': 'URL',
            'Target Audience': 'Audience',
            'Published Date': 'Date',
        }
        act = {}
        for k, v in activity.items():
            act[mapping.get(k, k)] = v
        # Ensure Quantity default
        if 'Quantity' not in act or act.get('Quantity') in (None, ""):
            act['Quantity'] = 1
        return act

    @staticmethod
    def _extract_json_array(raw: str) -> str:
        """Extract a JSON array string from raw LLM output that may include code fences or prose."""
        if not isinstance(raw, str):
            raise ValueError("Expected string content from parser agent")
        s = raw.strip()
        # Strip common markdown code fences
        if s.startswith("```"):
            # remove first fence line
            first_newline = s.find("\n")
            if first_newline != -1:
                s = s[first_newline + 1 :]
            # remove trailing fence
            if s.endswith("```"):
                s = s[: -3] 
            s = s.strip()
            # If it started with ```json, first line may be 'json'
            if s.startswith("json\n"):
                s = s[5:]
        # Heuristic: take substring from first '[' to last ']'
        if '[' in s and ']' in s:
            start = s.find('[')
            end = s.rfind(']')
            candidate = s[start:end+1]
            return candidate
        return s

    def _fmt_duration(self, secs: float) -> str:
        h = int(secs // 3600)
        m = int((secs % 3600) // 60)
        s = secs - (h * 3600 + m * 60)
        return f"{h:02d}:{m:02d}:{s:06.3f}"

    async def run_automation(self, confirm_before_save: bool = True, batch_size: int = 3, interactive: bool = False, process_mode: str = 'batched'):
        """Run the complete automation workflow with enhanced error handling and interactive review.
        process_mode: 'sequential' | 'batched'
            - 'sequential': process each activity strictly one-by-one (ignores batch_size)
            - 'batched': process in batches of `batch_size` (current/default behavior)
        """
        overall_start = time.perf_counter()
        try:
            # Initialize automation agent
            print("🚀 Initializing automation agent...")
            await self.automation_agent.initialize()

            # Parse activities from markdown file
            print("📄 Parsing activities from markdown file...")
            activities_json = await self.parser_agent.parse_activities(self.markdown_file_path)

            # Convert to structured data
            if isinstance(activities_json, str):
                try:
                    cleaned = self._extract_json_array(activities_json) if hasattr(self, '_extract_json_array') else activities_json
                    activities = json.loads(cleaned)
                except json.JSONDecodeError:
                    print("❌ Failed to parse activities JSON. Raw response:")
                    print(activities_json)
                    return
            else:
                activities = activities_json

            # Ensure private descriptions exist before validation
            print("📝 Ensuring Private Descriptions for all activities...")
            await self.ensure_private_descriptions(activities)
            # Enforce character limits with rewrite
            print("📏 Enforcing character limits (rewriting to ~850 chars when exceeded)...")
            await self.enforce_char_limits(activities)

            print(f"✅ Found {len(activities)} activities to process")

            # Normalize and validate all activities first
            print("\n🔍 Validating activity data...")
            valid_activities = []
            normalized_activities = [self._normalize_activity_keys(a) for a in activities]
            for i, activity in enumerate(normalized_activities):
                is_valid, missing_fields = self.validate_activity_data(activity)
                if is_valid:
                    valid_activities.append(activity)
                    print(f"✅ Activity {i+1} '{activity.get('Title', 'Unknown')}' - Valid")
                else:
                    print(
                        f"❌ Activity {i+1} '{activity.get('Title', 'Unknown')}' - Missing: {missing_fields}"
                    )

            if not valid_activities:
                print("❌ No valid activities found. Please check your markdown file.")
                return

            print(f"\n📊 Processing {len(valid_activities)} valid activities...")

            def interactive_review(activity: dict) -> dict:
                """Show planned field entries and wait for Enter if interactive; no per-field prompts."""
                print("\n🧐 Planned field entries (no edits here)")
                for key, val in activity.items():
                    print(f" - {key}: {val}")
                if interactive and not self.automation_agent.fast_mode:
                    try:
                        _ = input("Press Enter to continue...")
                    except Exception:
                        pass
                return dict(activity)

            # Decide processing mode: sequential (one-by-one) or batched
            if process_mode not in ('sequential', 'batched'):
                print(f"⚠️ Unknown process_mode '{process_mode}', falling back to 'batched'")
                process_mode = 'batched'

            if process_mode == 'sequential':
                batches = [[act] for act in valid_activities]
            else:
                batches = [valid_activities[i : i + batch_size] for i in range(0, len(valid_activities), batch_size)]

            # Process activities in determined batches
            for batch_num, batch in enumerate(batches, start=1):
                print(f"\n🔄 Processing batch {batch_num} ({len(batch)} activities)")
                batch_start = time.perf_counter()

                for j, activity in enumerate(batch):
                    activity_num = i + j + 1
                    activity_title = activity.get("Title", "Unknown")
                    print(
                        f"\n📝 Processing activity {activity_num}/{len(valid_activities)}: {activity_title}"
                    )

                    # Show activity details
                    print(f"   Category: {activity.get('Category', 'N/A')}")
                    print(f"   Main Technology: {activity.get('Main Technology', 'N/A')}")
                    print(f"   Date: {activity.get('Date', 'N/A')}")
                    print(f"   Views: {activity.get('Views', 'N/A')}")

                    # Interactive edit
                    reviewed = interactive_review(activity)

                    max_activity_retries = 3
                    start_ts = time.perf_counter()
                    for retry in range(max_activity_retries):
                        try:
                            _ = await self.automation_agent.submit_activity(
                                reviewed,
                                confirm_before_save=confirm_before_save,
                                interactive=interactive,
                            )
                            elapsed = time.perf_counter() - start_ts
                            print(f"⏱️ Time to add '{reviewed.get('Title', activity_title)}': {self._fmt_duration(elapsed)}")
                            print(f"✅ Successfully processed: {reviewed.get('Title', activity_title)}")
                            break

                        except Exception as e:
                            error_msg = str(e)
                            print(
                                f"❌ Error processing {activity_title} (attempt {retry + 1}): {error_msg}"
                            )

                            if retry < max_activity_retries - 1:
                                if "snapshot" in error_msg.lower() or "ref" in error_msg.lower():
                                    print("🔄 Snapshot error detected, retrying with fresh snapshot...")
                                    backoff = 0.25 if self.automation_agent.fast_mode else 0.5
                                    try:
                                        await asyncio.sleep(backoff)
                                    except Exception:
                                        pass
                                elif 'rate' in error_msg.lower() or '429' in error_msg:
                                    # use adaptive backoff if available
                                    if hasattr(self.automation_agent, '_compute_backoff'):
                                        backoff = self.automation_agent._compute_backoff()
                                    else:
                                        backoff = 0.25 if self.automation_agent.fast_mode else 0.5
                                    print(f"⏳ Rate limit detected, backing off {backoff:.2f} s...")
                                    await asyncio.sleep(backoff)
                                else:
                                    print("🔄 Retrying activity submission...")
                                    backoff = 0.5 if self.automation_agent.fast_mode else 1.0
                                    await asyncio.sleep(backoff)
                            else:
                                elapsed = time.perf_counter() - start_ts
                                print(f"⏱️ Time spent before failure for '{activity_title}': {self._fmt_duration(elapsed)}")
                                print(
                                    f"💥 Failed to process {activity_title} after {max_activity_retries} attempts"
                                )

                                # Ask user if they want to continue
                                if interactive and not self.automation_agent.fast_mode:
                                    try:
                                        continue_processing = input("Continue with next activity? (y/n): ").lower() == 'y'
                                    except Exception:
                                        continue_processing = True
                                else:
                                    continue_processing = True
                                if not continue_processing:
                                    print("🛑 User chose to stop processing")
                                    return
                                break

                batch_elapsed = time.perf_counter() - batch_start
                print(f"⏱️ Batch {batch_num} time: {self._fmt_duration(batch_elapsed)}")

                # Pause between batches (only meaningful in batched mode)
                if process_mode == 'batched':
                    # Determine if there are more batches remaining
                    remaining = sum(len(b) for b in batches[(batch_num):])
                    if remaining > 0:
                        print(f"\n⏸️  Completed batch {batch_num}. Pausing before next batch...")
                        if interactive and not self.automation_agent.fast_mode:
                            try:
                                _ = input("Press Enter to continue with next batch...")
                            except Exception:
                                pass

            print("\n🎉 Automation workflow completed!")

        except Exception as e:
            print(f"❌ Critical error in automation workflow: {str(e)}")

        finally:
            total_elapsed = time.perf_counter() - overall_start
            print(f"⏱️ Total run time: {self._fmt_duration(total_elapsed)}")
            # Clean up
            print("🧹 Cleaning up resources...")
            try:
                await self.automation_agent.close()
            except Exception as e:
                print(f"⚠️ Cleanup warning: {e}")

async def main():
    # Environment setup for Azure OpenAI
    import os
    
    # Ensure Azure OpenAI environment variables are set
    required_env_vars = ['AZURE_OPENAI_API_KEY', 'AZURE_OPENAI_ENDPOINT']
    missing_vars = [var for var in required_env_vars if not os.getenv(var)]
    
    if missing_vars:
        print(f"❌ Missing required environment variables: {missing_vars}")
        print("Please set (PowerShell):")
        print("$env:AZURE_OPENAI_API_KEY='your_api_key'")
        print("$env:AZURE_OPENAI_ENDPOINT='your_endpoint'")
        print("$env:AZURE_DEPLOYMENT='your_deployment_name'  # Optional")
        return
    
    # Path to your markdown file
    markdown_file = "activities-sample.md"
    
    if not os.path.exists(markdown_file):
        print(f"❌ Markdown file not found: {markdown_file}")
        return
    
    # Create orchestrator (fast mode can be enabled via FORMPILOT_FAST_MODE env var)
    fast_mode = os.getenv('FORMPILOT_FAST_MODE', 'true').lower() in ('1', 'true', 'yes')
    
    # Configure target website URL (can be set via environment variable)
    target_website_url = os.getenv('FORMPILOT_WEBSITE_URL', 'https://aka.ms/community-activities')
    
    print(f"🚀 Starting FormPilot automation in {'fast' if fast_mode else 'normal'} mode...")
    print(f"🌐 Target website: {target_website_url}")
    
    orchestrator = FormAutomationOrchestrator(
        markdown_file, 
        fast_mode=fast_mode, 
        target_website_url=target_website_url
    )
    
    # Add custom rules if needed
    custom_rules = """
    CUSTOM RULES:
    1. Always take screenshots at key steps for verification
    2. Wait 2 seconds between form field interactions
    3. Verify dropdown selections are actually selected
    4. For calendar widget, always double-check the selected date
    5. If any field appears empty after filling, retry immediately
    6. Take snapshot before every save operation
    7. Handle any unexpected popups or dialogs gracefully
    """
    
    # Apply custom rules to automation agent before initialization
    orchestrator.automation_agent.add_custom_rules(custom_rules)
    
    # Run automation with enhanced error handling and interactive mode
    # Set to True to require a pause and confirmation before saving each activity
    confirm_before_save = False  
    # Batch size used only when process_mode == 'batched'
    batch_size = 5  
    # Whether to allow an interactive pause for each activity
    interactive = False 
    # 'sequential' will process one-by-one; 'batched' will process in groups of batch_size
    process_mode = 'sequential'  # 'sequential' or 'batched'

    await orchestrator.run_automation(
        confirm_before_save=confirm_before_save,
        batch_size=batch_size,
        interactive=interactive,
        process_mode=process_mode,
    )

if __name__ == "__main__":
    asyncio.run(main())