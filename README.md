# FormPilot

A local automation framework that reads structured markdown files and fills out web forms automatically using Playwright MCP and Azure OpenAI. Built for developers and content creators who need to automate repetitive form submissions while maintaining complete control over their data.

[![Portfolio](https://img.shields.io/badge/Portfolio-shivamgoyal03.github.io-0366d6?style=flat-square&logo=github&logoColor=white)](https://shivamgoyal03.github.io/)
[![Disclaimer](https://img.shields.io/badge/Disclaimer-FormPilot-e03c31?style=flat-square)](disclaimer.md)
[![License](https://img.shields.io/badge/License-MIT-2ea44f?style=flat-square)](LICENSE)
[![GitHub Profile](https://img.shields.io/badge/GitHub-ShivamGoyal03-181717?style=flat-square&logo=github&logoColor=white)](https://github.com/ShivamGoyal03/)

> **All processing happens locally on your device - no data is sent to external services except for Azure OpenAI API calls.**

## Features

- **Markdown-Driven**: Define your form data in simple, structured markdown files
- **AI-Powered Automation**: Uses Azure OpenAI (GPT-5/GPT-4o) for intelligent form filling
- **Playwright MCP Integration**: Robust browser automation via Model Context Protocol
- **Batch Processing**: Handle multiple entries with sequential or batched processing
- **Smart Error Handling**: Adaptive retry logic with snapshot-based error recovery
- **Interactive Review**: Optional confirmation before each submission
- **Fast Mode**: Optimized performance for bulk operations
- **Intelligent Caching**: Caches dropdown options and generated content
- **Fuzzy Matching**: Smart technology field selection based on content analysis

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js (for Playwright MCP)
- Azure OpenAI API access

### Installation

1. **Clone the repository:**
```bash
git clone https://github.com/yourusername/formpilot-mcp.git
cd formpilot-mcp
```

2. **Install Python dependencies:**
```bash
pip install -r requirements.txt
```

3. **Set up environment variables** (create `.env` file or set in PowerShell):
```bash
# PowerShell
$env:AZURE_OPENAI_API_KEY='your_api_key'
$env:AZURE_OPENAI_ENDPOINT='your_endpoint'
$env:AZURE_DEPLOYMENT='your_deployment_name'  # Optional

# Optional configurations
$env:FORMPILOT_FAST_MODE='true'                    # Enable fast mode
$env:FORMPILOT_WEBSITE_URL='https://example.com'  # Target website
```

### Basic Usage

1. **Prepare your data** in `activities-sample.md` (see format below)

2. **Run the automation:**
```bash
python orchestrator.py
```

The automation will:
- Parse your markdown file
- Initialize Playwright MCP browser automation
- Navigate to the target website
- Fill forms automatically with smart field matching
- Handle errors and retries automatically

## Data Format

Structure your activities in markdown using this format:

```markdown
## Activity Title

**Activity Type:** Blog  
**Primary Technology Area:** Artificial Intelligence  
**Additional Technology Areas:** Python, Machine Learning  
**Title:** Building Intelligent Applications with Modern AI Frameworks  
**Description:** A comprehensive guide exploring the latest trends in AI application development...  
**Private Description:** Technical blog post about AI development best practices.  
**Number of Views:** 2500  
**Activity URL:** https://example.com/blog/ai-development-frameworks  
**Target Audience:** Developer, IT Pro, Technical Decision Maker  
**Published Date:** 2024-01-15  
**Quantity:** 1  
**Use Preview Image From Activity URL:** true  
```

### Required Fields
- **Activity Type**: Blog, Article, Speaking, Training, Code Sample
- **Primary Technology Area**: Main technology focus
- **Title**: Activity title
- **Description**: Detailed description
- **Activity URL**: Where the activity can be accessed
- **Target Audience**: Developer, IT Pro, Business Decision Maker, etc.
- **Published Date**: Date in YYYY-MM-DD format
- **Quantity**: Number of instances (defaults to 1)

### Optional Fields
- **Private Description**: Internal summary (auto-generated if blank)
- **Number of Views**: View count (skipped if omitted)
- **Additional Technology Areas**: Up to two related technologies
- **Start Date / End Date**: For events/workshops
- **Use Preview Image From Activity URL**: Boolean (defaults to true)

## Project Structure

```
FormPilot/
├── README.md                    # This documentation
├── disclaimer.md               # Usage disclaimer
├── orchestrator.py            # Main orchestration and workflow
├── automation_agent.py        # Core browser automation agent
├── activities-sample.md       # Sample data format
├── requirements.txt           # Python dependencies
├── .env                       # Environment variables (create this)
└── venv/                      # Virtual environment (auto-created)
```

## How It Works

### Architecture

```
┌─────────────────────┐
│   orchestrator.py   │  ← Coordinates workflow, handles parsing & batching
└─────────────────────┘
           │
           ▼
┌─────────────────────┐
│ automation_agent.py │  ← Browser automation via Playwright MCP
└─────────────────────┘
           │
           ▼
┌─────────────────────┐
│   Playwright MCP    │  ← Handles browser control and form interaction
└─────────────────────┘
```

### Key Components

1. **FormAutomationOrchestrator** (`orchestrator.py`)
   - Parses markdown files using Azure OpenAI
   - Validates and processes activity data
   - Manages batch processing and error handling
   - Generates missing descriptions and enforces character limits

2. **WebFormAutomationAgent** (`automation_agent.py`)
   - Initializes Playwright MCP for browser automation
   - Implements smart technology field selection
   - Handles form filling with retry logic
   - Manages snapshot-based error recovery

3. **Smart Features**
   - **Fuzzy Matching**: Intelligently matches technology options to form dropdowns
   - **Fallback Options**: Uses predefined technology lists when dropdown extraction fails
   - **Auto-Generation**: Creates internal notes from descriptions when missing
   - **Character Limits**: Automatically rewrites content to fit form constraints

## Configuration

### Processing Modes

```python
await orchestrator.run_automation(
    confirm_before_save=False,     # Require confirmation before each save
    batch_size=5,                  # Items per batch (batched mode only)
    interactive=False,             # Interactive review mode
    process_mode='sequential'      # 'sequential' or 'batched'
)
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AZURE_OPENAI_API_KEY` | Your Azure OpenAI API key | Required |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL | Required |
| `AZURE_DEPLOYMENT` | Deployment name | Optional |
| `FORMPILOT_FAST_MODE` | Enable fast mode (true/false) | true |
| `FORMPILOT_WEBSITE_URL` | Target website URL | https://aka.ms/community-activities |

### Custom Rules

Add automation rules to fine-tune behavior:

```python
custom_rules = """
CUSTOM RULES:
1. Always take screenshots at key steps for verification
2. Wait 2 seconds between form field interactions
3. Verify dropdown selections are actually selected
4. Handle any unexpected popups or dialogs gracefully
"""

orchestrator.automation_agent.add_custom_rules(custom_rules)
```

## Advanced Usage

### Technology Field Mapping

The automation agent includes intelligent technology field selection:

- **Primary Technologies**: AI, Cloud Computing, Web Development, Database Technology, Cybersecurity, etc.
- **Additional Technologies**: Python, JavaScript, React, Azure Functions, etc.
- **Fuzzy Matching**: Analyzes activity title and description to select best matches
- **Fallback System**: Uses predefined options when form extraction fails

### Error Handling

Comprehensive error handling includes:

- **Snapshot Errors**: Automatic page refresh and retry
- **Rate Limiting**: Adaptive backoff with exponential delay
- **Element Detection**: Smart retry for stale element references
- **Network Issues**: Configurable timeout and retry logic

### Batch Processing

- **Sequential Mode**: Process one activity at a time
- **Batched Mode**: Process multiple activities in configurable batches
- **Progress Tracking**: Detailed timing and status information
- **Failure Recovery**: Continue processing after individual failures

## Security & Privacy

- **Local Processing**: All data processing happens on your machine
- **API Usage**: Only Azure OpenAI API calls are made externally
- **No Data Storage**: Framework doesn't persist sensitive data
- **Browser Isolation**: Uses isolated browser profiles for automation

## Use Cases

- **Content Management**: Automate submissions to multiple platforms
- **Research Documentation**: Bulk entry of research activities
- **Portfolio Management**: Batch updates to professional profiles
- **Event Registration**: Automated conference and workshop submissions

## Troubleshooting

### Common Issues

**"Missing required fields" errors:**
- Check that all required fields are present in your markdown
- Verify field names match the expected format exactly

**Technology options showing as empty:**
- The system uses fallback technology options automatically
- Check logs for "FALLBACK_OPTIONS" messages

**Playwright MCP connection fails:**
```bash
# Install Playwright MCP via NPX (automatically handled)
npx -y @playwright/mcp@latest
```

**Azure OpenAI authentication errors:**
```bash
# Verify environment variables are set
echo $env:AZURE_OPENAI_API_KEY
echo $env:AZURE_OPENAI_ENDPOINT
```

### Debug Mode

Enable verbose logging by checking the console output for detailed execution logs including:
- Technology selection process
- Field matching status
- Error recovery attempts
- Timing information

## License & Disclaimer

This project is for educational and productivity purposes. Users are responsible for:

- Ensuring compliance with target website terms of service
- Respecting rate limits and usage policies  
- Using the tool ethically and responsibly
- Not violating any applicable laws or regulations

See [disclaimer.md](disclaimer.md) for complete disclaimer.

---

**Built with Playwright MCP, Agno, and Azure OpenAI**

*FormPilot - Automate web forms locally and intelligently.*