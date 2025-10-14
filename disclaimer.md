
# Disclaimer

## Purpose & Scope
This tool automates logging structured activity data from a local markdown file into web forms. It is designed to reduce repetitive manual entry, standardize activity metadata, and allow multitasking by automating browser interactions.

> **Note:** This is not a shortcut to instantly submit all activities within few seconds. Human oversight is required. Activities are either logged continuously or confirmed before submission, depending on your review mode.

## Usage & Compliance

- **General Use:** Works with any portal or web form needing structured activity data. No internal Microsoft tools or APIs required.
- **Local Data Only:** Playwright MCP fills forms locally. No backend storage by Microsoft; only the portal receives submitted entries via your authenticated session.
- **Playwright MCP:** Automates browser interactions, simulating a human filling forms. No data is stored on Microsoft servers; all data comes from your local markdown file.
- **Human Review:** Each entry requires confirmation before submission. Two modes:
  - **Batch mode:** Activities are processed sequentially and reviewed at the end.
  - **Step-by-step mode:** User confirms each activity before submission.
- **Data Accuracy:** You are responsible for the accuracy of your data. Validate your structured data before running automation.
- **No Official Endorsement:** Microsoft does not validate, endorse, or support any submissions made using this tool. This is your own initiative.
- **Experimental Status:** This is a personal, experimental tool. Not an official solution; treat as a prototype. Use responsibly and verify outputs before final submission.

## Compliance, NDA, and Safety

- **No NDA or confidential data:** Only local markdown data is used. No internal or restricted information from any platform is included.
- **No backend storage:** Playwright MCP interacts with the browser locally. No backend storage by Microsoft; only the portal receives submitted entries via your authenticated session.
- **Experimental & Unsupported:**
  - The tool may break if fields, layouts, or workflows change.
  - No platform will troubleshoot issues or validate data submitted through this automation.
- **No official platform association:** Even if it interacts with a web portal, this tool is not an official solution. Use as a general automation template for educational or personal purposes.
- **Human review required:** Each activity entry requires confirmation before submission to ensure data accuracy.


## Usage Notes

- This tool does not instantly log all activities. It processes them sequentially and is designed for multi-tasking.
- Only structured activity entries are supported; complex or event-specific entries may require manual adjustments.
- Dates and other fields must follow proper formatting.
- The tool automatically validates fields, rewrites overly long text, and enforces canonical names where applicable.
- The tool may cache field options and generated content to optimize performance.
- By using this tool:
    - You understand that Microsoft or any platform does not validate or endorse any submissions made using it.
    - You are responsible for the accuracy of the data you enter.

## Contact

For questions, feedback, or clarifications:

- **Shivam Goyal**
  - LinkedIn: [https://linkedin.com/in/shivam2003](https://linkedin.com/in/shivam2003)
  - GitHub: [https://github.com/ShivamGoyal03](https://github.com/ShivamGoyal03)

---

> This tool is intended for learning, productivity, and experimentation. Please use responsibly and do not rely on it as an official solution for any platform.